# Forge transport assessment

Status: **Historical issue #9 transport decision. The execution-only Forge transport was later removed under #991 / ADR 0014.**

This document preserves the transport analysis that led issue #9 to reject the legacy Forge task-launch API and introduce a separate execution-only sidecar. The lifecycle-boundary conclusions remain useful architecture history, but the adapter, HTTP client, sidecar CI lane and active compatibility claim described below are no longer present in the platform. See [`FORGE_RETENTION_DECISION.md`](FORGE_RETENTION_DECISION.md) for the final #991 removal decision.

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

The legacy manual launch path called Forge task services before starting an execution. Using it as the platform's concrete execution transport would therefore have required mirrored Forge Tasks and surrounding project/workflow assumptions. That would have created a shadow lifecycle and violated the platform-owned Task/Run model.

## Legacy API decision — unchanged

**The legacy `POST /api/v1/tasks/{id}/launch` path remains rejected as a platform execution transport.**

This is an architecture compatibility decision, not a defect claim about Forge's original application. The platform still requires:

- one canonical Task/Run lifecycle owned by the platform kernel;
- canonical IDs and contracts owned by the platform;
- no backend Task/Project source of truth;
- adapter-owned translation;
- backend optionality and clean removal without canonical-state migration.

## Historical exit criterion and sidecar

The original assessment said Forge reuse could proceed once an executor-only start boundary existed that did not require Forge Task lifecycle ownership. `ScoreSymphony/AI-Agent-VPS` PR #71 introduced `core/forge/crates/executor-sidecar` and merged it at historical pin:

`00b821bc94767865457814bf282982ca242a2e10`

The sidecar reused Forge execution implementation through:

- `crates/executors`;
- `crates/cli-adapters`;
- `crates/git`;
- `crates/api-types`.

It did not require the Forge DB, TaskService, Project lifecycle, Workflow engine or domain-event service.

## Historical transport shape

The removed transport chain was:

```text
PlatformKernel
    -> ExecutorLifecycleBackend
        -> ForgeExecutor
            -> ForgeClient
                -> ForgeHttpClient
                    -> forge-executor-sidecar/v1
```

The platform-owned HTTP client implemented the platform-owned Forge client protocol. The sidecar protocol exposed only execution-level health, submit, lookup, cancellation and log operations.

The compatibility target was `forge-executor-sidecar/v1` at the exact pin above. #991 removed this executable path after its generic platform guarantees had independent evidence.

## Identity and lifecycle ownership

The historical transport preserved canonical identities across the boundary:

- canonical `task_id` remained the Task identity;
- canonical `run_id` remained the Run identity;
- canonical `step_id` remained independent of Task identity;
- canonical `correlation_id` remained platform-owned;
- `request_ref` was derived from canonical Run identity only as a backend idempotency/recovery key;
- the sidecar-generated execution ID remained backend-private namespaced metadata.

The sidecar never transitioned canonical Task/Run state. It reported backend execution state while the platform kernel retained lifecycle authority. These ownership rules remain canonical after Forge removal.

## Recovery and idempotency lessons retained

The integration deliberately separated responsibilities:

- the platform kernel owned canonical historical event replay, lifecycle reconstruction and Run reconciliation;
- the sidecar owned backend-private dispatch idempotency and request-to-backend identity mapping;
- restarted in-flight backend work required reconciliation rather than blind redispatch;
- duplicate submission of the same request identity resolved to the same backend execution;
- conflicting reuse of an idempotency key was rejected;
- late backend completion could not overwrite terminal cancellation or timeout.

Those useful guarantees were generalized into backend-neutral kernel/lifecycle/reference coverage before the Forge-specific regression tests were removed.

## Historical security and deployment boundary

The sidecar profile used during integration was deliberately conservative:

- loopback binding;
- explicit executor allowlist;
- deterministic `null` executor as the validated integration target;
- independent workspace-containment checks at platform and sidecar layers;
- no Forge database or project/task services.

The profile did not establish sufficient non-null Forge CLI execution evidence to justify long-term first-party support. This distinction became a central reason for the ADR 0013 deprecation and ADR 0014 removal.

No Forge process, listener, workspace mapping or sidecar resource is active in supported platform deployment/CI paths after #991.

## Historical validation evidence

`AI-Agent-VPS` PR #71 compiled and tested the execution-only sidecar. Its regression suite covered workspace escape, executor allowlisting, idempotent submission, persisted idempotency across restart, conflicting request identity, interrupted in-flight recovery and cancellation terminality.

Platform PR #148 added the platform-owned HTTP client, protocol/identity tests and a real cross-repository integration job. That job checked out the exact pinned runtime revision, built the Rust sidecar, started it on loopback with only `null` enabled, and exercised real health, execution and cancellation through the adapter stack.

That evidence remains historically valid for the integration that existed at the time. It is not an active compatibility or CI claim after #991.

## Final status

Three conclusions now apply:

1. **The old Forge Task-based public launch API remains unsuitable and rejected.**
2. **The execution-only sidecar successfully proved that Forge could sit below the platform-owned `Executor`/lifecycle boundary.**
3. **The executable Forge integration was later retired under #991 once its generic guarantees were proven independently; no specialized replacement backend was promoted by that removal.**

This preserves the useful architectural lesson from issue #9 without presenting removed Forge code as a current platform capability.
