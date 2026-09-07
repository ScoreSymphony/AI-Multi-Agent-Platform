# Forge transport assessment

Status: **Execution-only transport integrated; legacy Forge task-launch API remains rejected.**

This document records the final transport decision for issue #9. The original assessment correctly rejected the legacy Forge task-launch HTTP path because it would import Forge Task/Project lifecycle ownership. That decision still stands. The exit criterion from the original assessment has since been satisfied by a separate execution-only runtime boundary.

## Historical source assessment

Repository: `ScoreSymphony/AI-Agent-VPS`

Original audited revision: `5a9f317e3bab056a4cebe214b03912a9b7ad3824`

Relevant legacy routes reviewed included:

- `GET /healthz`
- `GET /api/v1/executions/{id}`
- `POST /api/v1/executions/{id}/cancel`
- `GET /api/v1/executions/{id}/logs`
- `GET /api/v1/tasks/{id}/executions`
- `POST /api/v1/tasks/{id}/launch`

The legacy manual launch path calls Forge task services before starting an execution. Using it as the platform's concrete `ForgeClient.execute` transport would therefore require mirrored Forge Tasks and surrounding project/workflow assumptions. That would create a shadow lifecycle and violate the platform-owned Task/Run model.

## Legacy API decision — unchanged

**The legacy `POST /api/v1/tasks/{id}/launch` path remains rejected as the platform execution transport.**

This is an architecture compatibility decision, not a defect claim about Forge's original application. The platform still requires:

- one canonical Task/Run lifecycle owned by the platform kernel;
- canonical IDs and contracts owned by the platform;
- no Forge Task/Project source of truth;
- adapter-owned translation;
- Forge optionality and clean removal without canonical-state migration.

## Exit criterion — satisfied

The original assessment said the decision could be revisited once an executor-only start boundary existed that did not require Forge Task lifecycle ownership. That boundary now exists.

`ScoreSymphony/AI-Agent-VPS` PR #71 introduced `core/forge/crates/executor-sidecar` and merged it at:

`00b821bc94767865457814bf282982ca242a2e10`

The sidecar reuses the mature Forge execution implementation through:

- `crates/executors`
- `crates/cli-adapters`
- `crates/git`
- `crates/api-types`

It does **not** require the Forge DB, TaskService, Project lifecycle, Workflow engine or domain-event service.

## Integrated concrete transport

The final transport chain is:

```text
PlatformKernel
    -> ExecutorLifecycleBackend
        -> ForgeExecutor
            -> ForgeClient
                -> ForgeHttpClient
                    -> forge-executor-sidecar/v1
```

`src/ai_multi_agent_platform/adapters/forge_http.py` is a platform-owned, standard-library HTTP client implementing the existing platform-owned `ForgeClient` protocol.

The sidecar protocol exposes only the execution-level operations required by the adapter:

- `GET /healthz`
- `POST /v1/executions`
- `GET /v1/executions/{execution_id}`
- `GET /v1/requests/{request_ref}`
- `POST /v1/executions/{execution_id}/cancel`
- `GET /v1/executions/{execution_id}/logs`

The protocol compatibility target is explicitly `forge-executor-sidecar/v1`.

## Identity and lifecycle ownership

The transport preserves the canonical platform identities across the boundary:

- canonical `task_id` remains the Task identity;
- canonical `run_id` remains the Run identity;
- canonical `step_id` remains independent of task identity;
- canonical `correlation_id` remains platform-owned;
- `request_ref` is derived from the canonical Run ID and is used only as a backend idempotency/recovery key;
- the sidecar-generated Forge execution ID remains backend-private and is stored under namespaced adapter metadata.

`ForgeHttpClient` verifies the returned request/task/run/step identities before accepting a terminal result. A protocol or identity mismatch is treated as an adapter/runtime error rather than silently translated.

The sidecar never transitions canonical Task/Run state. It reports backend execution state; the platform kernel remains the lifecycle authority.

## Recovery and idempotency boundary

The transport deliberately splits recovery responsibilities:

- the **platform kernel** owns canonical historical event replay, lifecycle reconstruction and Run reconciliation;
- the **sidecar** owns backend-private dispatch idempotency and durable mapping from `request_ref` to backend execution identity;
- a restarted sidecar does not blindly redispatch a previously running request; persisted in-flight jobs become interrupted/reconciliation-required;
- duplicate submission of the same request identity resolves to the existing backend execution;
- reuse of the same idempotency key with conflicting canonical identity is rejected;
- a late backend completion cannot overwrite an already terminal cancellation or timeout.

This preserves proven Forge recovery/idempotency behavior without introducing a second canonical event or lifecycle store.

## Security and deployment boundary

The integrated sidecar is deliberately conservative:

- its current entrypoint binds to loopback only;
- executor families are controlled by an explicit allowlist;
- the deterministic `null` executor is the default validated integration target;
- both platform adapter and sidecar independently validate workspace containment;
- no Forge database or project/task services are needed;
- enabling Shell, Codex, Claude Code, Gemini, Cursor, OpenCode, Smith or other process-backed families requires explicit policy/security validation rather than implicit activation.

Forge remains optional and is not required for platform core/reference execution.

## Validation evidence

### Upstream runtime

`AI-Agent-VPS` PR #71 compiled and tested the execution-only sidecar. Its regression suite covers workspace escape, executor allowlisting, idempotent submission, persisted idempotency across restart, conflicting request identity, interrupted in-flight recovery and cancellation terminality.

### Platform client

Platform PR #148 added `ForgeHttpClient`, protocol/identity unit tests and a real cross-repository integration job.

The integration job checks out the exact pinned runtime revision `00b821bc94767865457814bf282982ca242a2e10`, builds the Rust sidecar, starts it on loopback with only `null` enabled, verifies health, then runs real execution and cancellation through:

`ForgeExecutor -> ForgeHttpClient -> Rust sidecar -> Forge null adapter`

CI run #628 passed the normal Python, frontend and LiteLLM gates and the real Forge sidecar integration job. PR #148 was then merged as `c9ebf166ebe91ba17c47fdc69b5383fc9711abc2`.

## Final decision

The two conclusions are intentionally simultaneous:

1. **The old Forge Task-based public launch API remains unsuitable and remains rejected.**
2. **A separate execution-only Forge transport is now integrated and validated.**

This is the transport outcome intended by issue #9: reuse the mature Forge executor engineering without adopting Forge's original Task/Project application architecture.
