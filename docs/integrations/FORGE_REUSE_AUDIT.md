# Forge reuse audit

Status: **Completed implementation audit for issue #9**

This document records the final reuse decisions for execution-related engineering from `ScoreSymphony/AI-Agent-VPS`, the canonical mappings used by this platform, and the evidence that the selected Forge behavior is now integrated without importing the legacy Forge lifecycle as platform architecture.

Issue #9 is already marked completed on GitHub. This revision reconciles the original Phase 1–3 audit text with the implementation that subsequently landed.

## Source and provenance

- Source repository: `https://github.com/ScoreSymphony/AI-Agent-VPS`
- Original audited baseline revision: `5a9f317e3bab056a4cebe214b03912a9b7ad3824`
- Integrated execution-only runtime revision: `00b821bc94767865457814bf282982ca242a2e10`
- Primary source subtree: `core/forge/`
- Integrated runtime subtree: `core/forge/crates/executor-sidecar`
- Reused runtime crates behind that sidecar: `executors`, `cli-adapters`, `git`, `api-types`
- Source repository license reviewed for issue #9: MIT
- Review/reconciliation date: 2026-09-03
- Machine-readable provenance: `upstream/forge-ai-agent-vps.yaml`

No Forge source is copied or vendored into this repository. The Python adapter and HTTP client are platform-owned implementations. The Rust sidecar remains in the canonical `AI-Agent-VPS` source repository and is consumed at an exact pinned revision.

## Architectural invariants

The completed integration preserves these rules:

1. `ai_multi_agent_platform.execution.Executor` remains the canonical execution seam.
2. `ExecutionRequest` and `ExecutionResult` remain platform-owned types.
3. Canonical `task_id`, `run_id`, `step_id` and `correlation_id` are never replaced by Forge identifiers.
4. Forge execution IDs are backend-private references stored only in namespaced adapter metadata.
5. The platform kernel owns canonical Task/Run lifecycle state.
6. Forge reports execution outcomes but is not a second lifecycle kernel.
7. Retry policy remains above the executor; Forge may report retryability/hints but does not schedule canonical retries.
8. Forge-private events and persistence are not canonical platform state.
9. Sidecar persistence exists only to make backend execution dispatch/recovery idempotent.
10. Disabling/removing Forge leaves core startup and reference execution functional.

## Phase 1–2 capability inventory and final classification

| Capability | Relevant Forge source/behavior | Final classification | Final platform treatment |
| --- | --- | --- | --- |
| Executor abstraction/outcomes | `crates/executors` | **Adapt/port** | Reused through an execution-only sidecar and translated by `ForgeExecutor`. |
| CLI executor adapters | `crates/cli-adapters` | **Reuse mostly unchanged** behind boundary | Kept in Rust upstream; not imported as canonical Python types. |
| Executor fallback routing | `executors/adapter.rs` | **Defer** as platform policy | May remain internal to an explicitly configured backend; it does not define global platform routing. |
| Shell/process execution | executor command/shell modules | **Reimplement/guard by policy** | Not enabled by default; concrete CLI/shell families require explicit allowlisting/security review. |
| Structured execution logs | executor log schema/reader/writer | **Adapt/port** | Sidecar exposes bounded logs; platform translates relevant stdout/stderr/evidence. |
| Legacy task/job dispatcher | task dispatcher services | **Reject** as platform scheduler | Canonical scheduling stays in platform Task/Run/Worker logic. |
| Active execution recovery | Forge recovery services | **Reimplement from behavior/spec** | Canonical reconciliation lives in `PlatformKernel`; sidecar only preserves backend execution identity/idempotency. |
| Heartbeat/stall behavior | recovery/daemon monitoring | **Adapt/port where needed** | Backend health can surface through executor health without becoming canonical Run state. |
| Durable event append/read pattern | domain-event service | **Reimplement from behavior/spec** | Platform event store/replay owns canonical events. |
| Historical event reads | domain-event repository/service | **Reimplement from behavior/spec** | Covered by platform historical replay/recovery tests rather than Forge event storage. |
| Event dedupe/idempotency | Forge dedupe/claim semantics | **Adapt/port behavior** | Canonical effects remain platform-owned; sidecar uses `request_ref` for backend dispatch idempotency. |
| Forge in-process event bus | events crate | **Reject** as canonical | May remain internal upstream but is not a platform transport. |
| Forge Task/Project/Workflow lifecycle | task/project/workflow services and DB | **Reject** | Never imported into canonical lifecycle. |
| Git worktree/workspace mechanics | workspace/git crates | **Adapt/port** | Workspace containment behavior retained behind adapter/runtime boundary. |
| Path escape/isolation behavior | workspace validation | **Reimplement from behavior/spec** | Enforced independently by both platform adapter and sidecar. |
| Workspace leases/cleanup | recovery/workspace services | **Defer/adapt later** | Not required by the first execution-only runtime. |
| Artifact/evidence collection | executor logs/output | **Reimplement from behavior/spec** | Returned evidence maps to canonical `ExecutionArtifact`/result fields. |
| Deterministic execution-baseline policy | execution baseline code | **Defer** | Not part of issue #9 runtime boundary. |
| Cancellation | `TaskExecutor::cancel` and adapters | **Adapt/port** | Canonical cancellation is bridged to sidecar/backend cancellation. |
| Retry/failure hints | executor failure classes | **Adapt reporting only** | Normalized into canonical errors/metadata; no adapter-owned retry loop. |
| Legacy public HTTP launch API | Forge task-launch API | **Reject** as execution transport | Requires Forge Task lifecycle and therefore remains intentionally unused. |
| Execution-only HTTP sidecar | `crates/executor-sidecar` | **Adapt/port / optional runtime** | Concrete `ForgeHttpClient` target using `forge-executor-sidecar/v1`. |
| Legacy API error taxonomy | API/executor errors | **Reimplement from behavior/spec** | Mapped into canonical execution error categories. |
| Legacy auth/authorization | Forge middleware/services | **Reject** as platform authority / defer transport auth | Platform authorization remains authoritative. |
| Health/readiness | executor/runtime health | **Adapt/port** | Exposed through `ForgeExecutor.health()`. |
| Observability/correlation | logs/tracing | **Adapt/port** | Canonical IDs are retained; backend fields remain namespaced. |
| Legacy deployment assumptions | SQLite/daemon/project application | **Reject** as platform defaults | Integrated sidecar requires no Forge DB, TaskService, Project service or domain-event service. |
| Agent chat/milestones/product orchestration | higher Forge services | **Reject** for #9 | Not part of the execution adapter. |

## Phase 3 canonical mapping

### Identity

| Forge/runtime concept | Canonical representation | Rule |
| --- | --- | --- |
| platform task identity carried into sidecar | `ExecutionRequest.task_id` | Returned identity must match exactly. |
| platform run identity | `ExecutionRequest.run_id` | Remains canonical and also supplies the sidecar `request_ref` idempotency key. |
| platform step identity | `ExecutionRequest.step_id` | Preserved independently from task identity. |
| platform correlation identity | `ExecutionRequest.correlation_id` | Preserved across adapter/runtime boundary. |
| sidecar/Forge execution ID | `adapter_metadata["forge"]["execution_id"]` | Never replaces canonical Run ID. |
| Forge executor family/config | adapter-private configuration/metadata | Does not expand canonical contracts. |

### Lifecycle and errors

Forge/backend completion is translated into canonical success/failure/cancel/timeout results. The kernel applies canonical lifecycle transitions. Relevant backend failures map to `invalid_request`, `unsupported_capability`, `workspace_error`, `execution_failed`, `timeout`, `cancelled` or `internal`; backend-specific codes and retry hints remain details/metadata.

### Workspace and evidence

`ForgeExecutor` resolves the canonical workspace below its configured root before dispatch. The Rust sidecar independently canonicalizes and verifies the submitted workspace against its own configured root. Returned artifact paths are checked again before becoming canonical evidence. Forge logs/output may populate stdout, stderr and result metadata without redefining artifact identity.

## Phase 4 final runtime architecture

```text
PlatformKernel
    -> ExecutorLifecycleBackend
        -> ForgeExecutor
            -> ForgeClient protocol
                -> ForgeHttpClient
                    -> forge-executor-sidecar/v1
                        -> Forge executors + cli-adapters
```

The execution-only sidecar is deliberately narrower than the legacy Forge application. It exposes health, submit/status, request-ref lookup, cancellation and logs. It does not require Forge Task/Project persistence or project/workflow orchestration.

The sidecar currently binds to loopback in its entrypoint and uses an explicit executor allowlist. The deterministic `null` executor is the default validated integration target. Other coding/shell CLI families are available in the upstream adapter registry but require explicit enablement and separate policy/security validation.

## Phase 5 recovery, historical events and idempotency

The old Forge mechanisms were not copied as a second canonical store. Their useful invariants were split according to ownership:

- **Canonical historical event replay:** platform-owned event/history storage and kernel replay.
- **Canonical restart reconciliation:** `PlatformKernel` recovery/reconciliation rules.
- **Duplicate canonical effects:** platform idempotency/revision/event tests.
- **Backend dispatch idempotency:** sidecar durably maps `request_ref` to backend execution identity.
- **Interrupted sidecar jobs:** persisted `running` jobs are marked interrupted/reconciliation-required after sidecar restart rather than blindly redispatched.
- **Cancellation race:** a late backend completion cannot overwrite a terminal cancellation/timeout state.
- **Externally observable lifecycle reconstruction:** canonical lifecycle is reconstructed from platform history; Forge remains an execution observation/source, not the state authority.

This separation preserves the valuable recovery behavior while honoring issue #9's explicit kernel-ownership requirement.

## Validation evidence

### Platform adapter/contract layer

PR #129 added the optional `ForgeExecutor`, the platform-owned `ForgeClient` seam, canonical identity/result/error translation, workspace and artifact guards, cancellation/health behavior, applicable executor contract coverage, lifecycle metadata propagation and Forge-specific kernel/recovery regressions.

The regression coverage includes persisted backend execution metadata, correct Task/Step identity separation and restart reconciliation that does not blindly redispatch a missing backend job.

### Rust runtime layer

`ScoreSymphony/AI-Agent-VPS` PR #71 introduced the execution-only sidecar and merged it at `00b821bc94767865457814bf282982ca242a2e10`.

Its Rust CI passed `cargo check` and all sidecar tests. Sidecar tests cover allowlisting, workspace escape, idempotent submission, idempotency across restart, identity conflicts, interrupted persisted jobs and cancellation terminality.

### Real cross-repository integration

Platform PR #148 added `ForgeHttpClient` and a CI job that:

1. checks out the exact pinned `AI-Agent-VPS` runtime revision;
2. builds `forge-executor-sidecar`;
3. starts it on loopback with only `null` allowlisted;
4. verifies health;
5. executes a real canonical request through Python `ForgeExecutor -> ForgeHttpClient -> Rust sidecar`;
6. verifies canonical task/run/correlation identity and namespaced Forge execution identity;
7. verifies end-to-end cancellation;
8. runs alongside normal Python, frontend and LiteLLM gates.

CI run #628 completed successfully across the normal platform gates and the real Forge sidecar integration job before PR #148 was merged as `c9ebf166ebe91ba17c47fdc69b5383fc9711abc2`.

## Rejected legacy assumptions

The following remain explicitly rejected:

- Forge is the canonical Task/Run source of truth.
- Forge SQLite schema defines platform persistence.
- Forge project/workflow state defines platform orchestration.
- Forge daemon/agent IDs replace platform Worker/Node identities.
- Forge events define the canonical event taxonomy.
- Forge executor routing defines global platform model/executor routing.
- Forge API types become public platform contracts.
- Every backend must use the Forge Git-worktree layout.
- Forge must be running for baseline platform startup/reference execution.
- Recovery may mutate canonical lifecycle outside the platform kernel.

## Deferred capabilities

The completed issue does not automatically enable or adopt:

- multi-candidate Forge executor fallback as global routing policy;
- unrestricted shell execution;
- Codex/Claude/Gemini/Cursor/OpenCode/Smith runtime allowlisting by default;
- remote/shared sidecar transport or remote authentication;
- deterministic Forge release-policy digests;
- Forge daemon topology;
- Forge-native projects, milestones, chat or product workflows.

Each can be evaluated independently later without reopening canonical lifecycle ownership.

## Acceptance criteria reconciliation

- [x] A documented reuse matrix exists with `reuse / adapt / reimplement / reject / defer` decisions.
- [x] Architecture-significant decisions include rationale.
- [x] Provenance and license metadata are recorded; no Forge source is copied into this repository.
- [x] Forge capabilities sit behind the canonical execution/lifecycle adapter boundary.
- [x] Forge-private IDs/types do not become canonical platform contracts.
- [x] Platform lifecycle ownership remains in the new kernel.
- [x] Forge adapter passes applicable executor contract tests.
- [x] Recovery/idempotency/event behavior is covered by platform and sidecar regression tests.
- [x] Removing/disabling the Forge adapter leaves core/reference execution functional.
- [x] Rejected legacy assumptions are documented.
- [x] A real optional Forge execution runtime is pinned and exercised end-to-end without importing the old Task/Project lifecycle.

## Definition of Done conclusion

Issue #9's intended result has been achieved: the platform recovered the genuinely useful Forge executor, workspace-isolation, logging, cancellation, idempotency and recovery engineering while retaining platform-owned canonical contracts and lifecycle state. Forge is now an optional execution backend, not the architecture of the platform.
