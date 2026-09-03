# Forge reuse audit

Status: **Phase 1–3 audit baseline for issue #9**

This document inventories the execution-related engineering in `ScoreSymphony/AI-Agent-VPS`, classifies what should be reused, and maps valuable Forge behavior onto the canonical contracts of this repository.

It is intentionally conservative. The old Forge implementation is a large Rust subsystem with its own task, execution, database, workflow, API, workspace and daemon assumptions. The new platform is Python-based and already owns its canonical `Task`, `Run`, event and `Executor` contracts. Therefore, source-level copying is not the default reuse strategy.

## Audit source and provenance

- Source repository: `https://github.com/ScoreSymphony/AI-Agent-VPS`
- Audited source revision: `5a9f317e3bab056a4cebe214b03912a9b7ad3824`
- Primary source subtree: `core/forge/`
- Source repository license at the audited revision: MIT
- Review date: 2026-09-03
- Local provenance record: `upstream/forge-ai-agent-vps.yaml`

No Forge source code is copied by this audit PR. A later selective code port must add file-level provenance and preserve required MIT notices before copied/adapted source is committed.

## Architectural invariants

The following rules are mandatory for all later Forge work:

1. `ai_multi_agent_platform.execution.Executor` remains the canonical execution seam.
2. `ExecutionRequest` and `ExecutionResult` remain platform-owned types.
3. Canonical `task_id`, `run_id`, `step_id` and `correlation_id` are never replaced by Forge identifiers.
4. Forge execution/job IDs are adapter-private external references and must be namespaced in `adapter_metadata` or equivalent adapter persistence.
5. The platform kernel owns canonical Task/Run lifecycle state.
6. Forge may report execution outcomes; it may not become a second canonical lifecycle kernel.
7. Executor implementations report normalized failure information and do not own platform retry policy.
8. Forge-private event schemas may be translated into canonical platform events but may not become the canonical event schema.
9. Forge-specific persistence may exist only as backend-private state. It is not a second source of truth for platform Task/Run state.
10. Disabling/removing the Forge adapter must leave core startup, the reference executor and baseline tests functional.

## Phase 1 — capability inventory and Phase 2 — reuse classification

Classification meanings follow issue #9: **Reuse mostly unchanged**, **Adapt/port**, **Reimplement from behavior/spec**, **Reject**, and **Defer**.

| Capability | Relevant old source | Existing evidence/tests observed | Classification | Rationale / target boundary |
| --- | --- | --- | --- | --- |
| Executor abstraction and execution outcomes | `core/forge/crates/executors/src/lib.rs`, `adapter.rs`, `shell.rs` | in-module executor/log tests; extensive adapter implementation | **Adapt/port** | Valuable backend execution behavior exists, but Forge's `ExecutionContext`, `ExecutionResult`, routing and failure types cannot become canonical. Translate behind platform `Executor`. |
| Executor routing/fallback | `executors/src/adapter.rs`, `config.rs`, `effective_policy.rs` | implementation plus crate tests | **Defer** | The new platform has separate model/executor selection responsibilities. First Forge adapter should target one configured Forge backend; routing can be reconsidered later without contaminating the canonical contract. |
| Shell/process execution | `executors/src/shell.rs`, `command.rs` | executor crate tests | **Reimplement from behavior/spec** | Useful process-management behavior, but the new reference executor intentionally is not a shell. Any unrestricted command executor needs explicit policy/sandbox controls and should be platform-owned or adapter-scoped. |
| Structured execution logs | `executors/src/log_schema.rs`, `log_writer.rs`, `log_reader.rs` | explicit write/read/tail/truncation tests in `executors/src/lib.rs` | **Adapt/port** | Sequence-aware log/evidence behavior is reusable. Map stdout/stderr and bounded diagnostics into canonical result/evidence; retain Forge-specific fields only under namespaced metadata. |
| Task/job dispatch | `services/src/task_dispatcher.rs`, `services/src/task_dispatcher/*`, `deferred_dispatch.rs` | `task_dispatcher/tests.rs` is wired as a dedicated test module | **Reimplement from behavior/spec** | Dispatcher behavior is useful, but the old dispatcher scans Forge projects/tasks and resolves Forge workflows from Forge DB state. New scheduling must use platform-owned Runs/Workers. |
| Active execution recovery | `services/src/recovery.rs`, `task_dispatcher/active_recovery.rs` | recovery implementation includes explicit state-machine behavior and tracing; test coverage should be harvested before porting | **Reimplement from behavior/spec** | Recovery is high-value but tightly coupled to Forge DB entities, agents, daemons, leases and task state. Preserve scenarios and invariants while moving canonical state repair into the platform kernel. |
| Heartbeat/stall detection | `services/src/recovery.rs`, daemon monitoring/services | monitored timeout/disconnect behavior exists | **Adapt/port** | Useful worker/backend liveness mechanism. Translate liveness into adapter/worker health; do not let Forge heartbeat state become canonical Run state. |
| Domain-event append-after-commit | `services/src/domain_event_service.rs`, `crates/db/*` | in-module tests for self-wake/depth suppression; repository methods expose append/get/dedupe/claim/complete | **Adapt/port** | The durable-before-notify pattern is valuable. Implement against the platform event store/transport when available; Forge's SQLite event record is not canonical. |
| Historical event read / leased consumption | `services/src/domain_event_service.rs`, `crates/db/*` | claim/complete processing and dedupe lookup are explicit in service API | **Adapt/port** | Preserve monotonic historical reads, leased claims and completion receipts as behavior. Map to canonical event IDs/sequences and platform consumer state. |
| Event deduplication/idempotency | `domain_event_service.rs`, `crates/db/*` | `get_by_dedupe`, dedupe-key based publication and completion paths exist | **Adapt/port** | Dedupe is valuable, but idempotency keys must be defined by the platform boundary. Forge dedupe keys become backend/external keys where needed. |
| In-process event bus | `core/forge/crates/events/src/lib.rs` | crate-level event implementation | **Reject** as canonical; **Defer** as adapter-local mechanism | It is process-local and Forge-specific. The platform will own event transport/messaging (#35). An internal Forge client may still use it without exposing it. |
| Forge task/project/workflow lifecycle | `services/src/task_service*`, `workflow/*`, `project_runtime.rs`, `project_orchestration.rs`, DB task/project models | large existing service surface | **Reject** as platform lifecycle | This is the main legacy architectural assumption issue #9 must not reintroduce. Platform Task/Run/kernel state is canonical. |
| Workspace creation / Git worktrees | `core/forge/crates/workspace/src/lib.rs`, `repo_cache.rs` | workspace crate contains explicit errors and path/lock behavior | **Adapt/port** | Worktree creation/recovery, cache locking and cleanup are useful mechanics. Map platform `workspace` reference to an adapter-resolved path; never derive canonical identity solely from Forge task IDs. |
| Workspace isolation / path escape prevention | `workspace/src/lib.rs`, `services/src/workspace_execution_lock.rs` | explicit `PathEscape`, lock and path validation behavior | **Reimplement from behavior/spec** | Security invariant is reusable and already has a platform equivalent. Keep platform workspace/write rules canonical and add regression cases based on Forge behavior. |
| Workspace leases/cleanup | `services/src/recovery.rs`, `workspace_cleanup.rs`, DB workspace lease repository | stale lease expiry/recovery logic exists | **Adapt/port** | Useful for distributed execution later, but lease ownership must align with platform Worker/Workspace contracts rather than Forge agents/daemons. |
| Artifact/evidence collection | executor logs; `execution_baseline.rs`; service artifact/evidence code | deterministic digest/render helpers exist for old execution baseline | **Reimplement from behavior/spec** | Evidence integrity ideas are useful, but Forge execution-baseline policy is product-specific. Canonical `ExecutionArtifact` and later artifact storage own the boundary. |
| Deterministic policy/digest rendering | `services/src/execution_baseline.rs` | deterministic schema/digest validation helpers | **Defer** | Potentially useful for approval/evidence systems, but not required for the first Forge executor and carries Forge-specific release-policy vocabulary. |
| Cancellation | `executors::TaskExecutor::cancel`, executor implementations | trait-level cancellation path exists | **Adapt/port** | Adapter must bridge platform `CancellationToken`/cancel request to Forge cancellation without changing canonical cancellation semantics. |
| Retry behavior | executor failure classes/routing plus task services | failure classification and retry-after information exist | **Adapt/port reporting only** | Preserve useful `retry_after`/availability signals in normalized error/metadata. The platform kernel/orchestrator decides retry policy. |
| Public HTTP/API contracts | `core/forge/crates/api/src/lib.rs`, `errors.rs`, `middleware.rs`, `routes/*`, `api-types` | large API implementation | **Reject** as canonical; **Adapt/port** only for Forge client transport | Existing Forge HTTP types must not leak into platform APIs. A Forge adapter may call a stable subset and translate responses. |
| API error taxonomy | `api/src/errors.rs`, executor error types | centralized old error mapping exists | **Reimplement from behavior/spec** | Map relevant cases into `ExecutionErrorCategory`: `invalid_request`, `unsupported_capability`, `workspace_error`, `execution_failed`, `timeout`, `cancelled`, `internal`. |
| Authentication/authorization boundaries | `api/src/middleware.rs`, `services/src/auth_service.rs`, `provider_authorization.rs`, OAuth services | old auth middleware/services exist | **Defer** for adapter transport; **Reject** as platform authority | Platform authentication/authorization issues own user/service policy. Forge credentials may authenticate the backend connection only. |
| Health/readiness / daemon status | API/service/daemon monitor modules | daemon/liveness monitoring exists | **Adapt/port** | Expose only backend health through `ExecutorDescriptor` / `health()` and later Worker health. Do not expose Forge daemon state as canonical platform state. |
| Observability/tracing | tracing instrumentation across dispatcher/recovery; executor log stack | structured tracing/logging is pervasive | **Adapt/port** | Preserve correlation and backend diagnostics, but key platform IDs must be canonical and backend-specific fields namespaced. |
| Configuration/deployment assumptions | executor configs, Forge config/daemon/API crates, SQLite/local worktree assumptions | mature legacy runtime configuration | **Reject** as platform defaults; **Defer** as Forge adapter configuration | Forge may require its own endpoint/runtime configuration, but the platform must remain deployment-neutral and Forge-optional. |
| Agent chat, milestones, project genesis, product/orchestration services | large parts of `services/src/*` | substantial legacy implementation | **Reject** for issue #9 | These are not execution-adapter capabilities and would recreate the old product architecture inside the new platform. |

## Key findings from inspected code

### Durable event pattern

`DomainEventService::append` commits the authoritative event through `DomainEventRepo` before publishing a bounded `domain_event.committed` notification. Claimed historical batches are completed only after the handler succeeds. This is a strong mechanism to preserve, but the new implementation must use platform-owned event records and consumer state.

### Recovery is valuable but deeply coupled

`CrashRecovery` expires workspace leases, scans in-progress Forge tasks, invokes the old recovery state machine, republishes status/recovery events and repairs interrupted entry barriers. `HeartbeatMonitor` additionally couples task recovery to agents, daemons, executions and Forge executor instances. The scenarios are valuable regression material; the implementation is not portable unchanged.

### Old dispatch owns too much state

`TaskDispatcher` scans Forge projects and tasks directly from SQLite and resolves a Forge `WorkflowEngine`. Porting it unchanged would create a second scheduler/lifecycle authority. Its scheduling/recovery scenarios should be rewritten against platform `Task`/`Run`/Worker contracts instead.

### Workspace mechanics contain reusable security behavior

`WorkspaceManager` creates/recover Git worktrees, manages per-task locks, cleans worktrees and rejects paths outside a canonicalized root. These are useful mechanics, but the new platform workspace contract and canonical IDs must remain above them.

### Forge executor types are not the new execution contract

The old Rust `TaskExecutor` accepts Forge `ExecutionContext` and returns a Forge-specific result containing session IDs, token usage, fallback candidates, route attempts and Forge failure classes. The new Python `Executor` already owns task/run/correlation identity, timeout, cancellation, workspace, artifacts and canonical errors. The adapter must translate rather than import the old type model.

## Phase 3 — mapping specification

### Identity mapping

| Legacy Forge concept | Canonical platform representation | Rule |
| --- | --- | --- |
| Forge task ID | `ExecutionRequest.task_id` only when it represents the same platform task; otherwise adapter external ref | Never replace a platform task ID. |
| Forge execution ID | namespaced backend reference, e.g. `adapter_metadata["forge"]["execution_id"]` | `ExecutionResult.run_id` remains the platform Run ID. |
| Forge project ID | adapter configuration/context only if required | Must not create a competing canonical Project identity. |
| Forge daemon/agent ID | backend Worker/executor metadata | Later map to canonical Worker/Node identities through their platform contracts. |
| Forge correlation/dedupe identifiers | platform `correlation_id` plus namespaced external metadata | Canonical correlation remains platform-owned. |

### Lifecycle mapping

| Forge outcome/state | Platform meaning |
| --- | --- |
| execution completed | return canonical successful `ExecutionResult`; kernel applies the allowed Run/Task transition |
| execution failed | return canonical failure with `ExecutionErrorCategory.EXECUTION_FAILED` unless a more specific category applies |
| execution cancelled | canonical cancelled result/error; preserve platform cancellation identity |
| executor unavailable/quota/auth unavailable | normalized failure/adapter diagnostics; may set retryable metadata, but adapter does not schedule retry |
| stale/interrupted execution found during reconciliation | report reconciliation observation to platform recovery path; platform kernel decides canonical state repair |

Legacy task statuses must not be translated by mutating platform persistence directly from the Forge adapter.

### Error mapping baseline

| Forge/backend condition | Canonical category |
| --- | --- |
| malformed/missing required adapter input | `invalid_request` |
| requested operation unsupported by configured Forge backend | `unsupported_capability` |
| worktree/workspace missing, locked or escaping root | `workspace_error` |
| backend process/task non-zero or domain execution failure | `execution_failed` |
| backend/execution timeout | `timeout` |
| acknowledged cancellation | `cancelled` |
| transport/protocol/unclassified adapter defect | `internal` |

Backend error codes/messages and Forge-specific failure classes belong under namespaced details/adapter metadata and must not expand the canonical enum without a platform-level decision.

### Event mapping baseline

Old Forge domain events should be treated as **external/backend observations** unless the action originated in the platform kernel.

- Preserve source event ID/sequence/dedupe key as namespaced external metadata.
- Use the platform event envelope and canonical entity IDs when emitting platform events.
- Never replay a Forge event by directly applying Forge task-state semantics to canonical Task/Run persistence.
- Dedupe duplicate callbacks/events at the adapter/event-ingress boundary.
- Historical reads may be used for reconciliation, but reconstruction of canonical lifecycle must pass through platform-owned recovery rules.

### Workspace/artifact mapping

- `ExecutionRequest.workspace` is resolved through the platform/adapter workspace boundary.
- Forge worktree paths are backend-local implementation details.
- Path traversal and root escape remain hard failures.
- Forge logs may populate canonical stdout/stderr and/or durable artifacts.
- Returned files must become canonical `ExecutionArtifact` references or later durable platform artifact identities.
- Forge cleanup must not silently destroy evidence before the platform has attached/materialized required artifacts.

## Initial adapter shape

The first implementation should be deliberately small:

```text
PlatformKernel
    -> ExecutorLifecycleBackend
        -> ForgeExecutor (platform adapter)
            -> ForgeClient/transport
                -> Forge backend
```

`ForgeExecutor` should initially implement only:

1. descriptor/health;
2. one execution submission path;
3. canonical identity propagation;
4. cancellation;
5. normalized result/error mapping;
6. workspace reference translation;
7. stdout/stderr/artifact evidence translation;
8. namespaced backend execution ID/diagnostics.

Do **not** port Forge project orchestration, Forge task persistence, milestone/runtime services, fallback routing, agent chat or product workflow logic as part of the first adapter.

## Test migration plan

### Layer 1 — existing behavior/spec harvesting

Harvest scenarios from old Rust tests before implementation changes, especially:

- executor completion/failure/cancellation;
- structured log sequencing/read/tail/truncation;
- workspace path escape and locking;
- task dispatcher recovery scenarios;
- domain event dedupe/claim/complete semantics;
- crash recovery and stale execution/lease behavior.

Tests should be rewritten in Python when they validate platform-owned behavior. Old Rust tests remain useful upstream regression evidence if Forge continues to run as an external backend.

### Layer 2 — canonical executor contract suite

The Forge adapter must run the existing `tests/executor_contract_suite.py` wherever the backend capability exists. Required coverage includes canonical identity preservation, controlled failure mapping, timeout, cancellation, unsupported capability, workspace isolation and artifact evidence.

Backend limitations must be explicit; they are not reasons to weaken the canonical contract silently.

### Layer 3 — migration/recovery regression tests

Add focused tests proving:

- Forge execution IDs stay external/namespaced;
- duplicate backend callbacks/events are idempotent;
- historical backend events can be reread without duplicate canonical effects;
- restart reconciliation does not bypass kernel lifecycle rules;
- Forge retry hints do not cause the adapter itself to schedule retries;
- disabling Forge leaves reference execution and core tests green.

## Rejected legacy assumptions

The following assumptions are explicitly rejected and must not re-enter the new architecture through later ports:

- Forge is the canonical Task/Run source of truth.
- Forge SQLite schema defines platform persistence.
- Forge project/workflow state defines platform orchestration.
- Forge daemon/agent IDs replace platform Worker/Node identities.
- Forge event types define the canonical event taxonomy.
- Forge executor routing defines global model/executor routing policy.
- Forge API types become public platform API types.
- A local Git worktree layout is required for every execution backend.
- Forge must be running for platform core/reference execution to start.
- Recovery is allowed to mutate canonical lifecycle outside the platform kernel.

## Deferred capabilities

Revisit only after the minimal Forge adapter and recovery/event ingress are proven:

- multi-candidate executor fallback/routing;
- deterministic execution-baseline/release-policy digests;
- advanced daemon topology and remote worker transport;
- provider/account authorization semantics;
- rich interactive agent session continuation;
- Forge-native orchestration/project/milestone features, if ever needed as optional higher-level adapters.

## Phase 4 implementation order

1. Add platform-owned `ForgeExecutor` adapter package with no core import reversal.
2. Define a minimal `ForgeClient` protocol so transport is independently testable.
3. Implement request/result/error/identity translation.
4. Apply the reusable executor contract suite with a fake/in-memory Forge client first.
5. Add an optional real transport/integration layer.
6. Add cancellation and health.
7. Add evidence/artifact translation.
8. Only then add historical-event/recovery ingress with idempotency tests.

## Gate before copying source

Before any old file or substantial code fragment is copied/adapted into this repository:

- choose and record integration category (`selective code port`, `adapter`, `external self-hosted service`, or `reference-only influence`);
- pin the exact source revision/path;
- verify license/notice obligations for that source;
- record modification status and local path;
- add/update the provenance registry;
- explain why source copying is preferable to reimplementation from behavior/spec;
- add the relevant contract/regression tests in the same PR.

## Current issue #9 progress after this audit

Completed by this document:

- [x] structured capability inventory baseline;
- [x] reuse/adapt/reimplement/reject/defer decisions with architecture rationale;
- [x] initial old-to-canonical identity/lifecycle/event/workspace/error mappings;
- [x] explicit rejected/deferred legacy assumptions;
- [x] source revision and license provenance baseline;
- [x] first-adapter implementation/test plan.

Still required before issue #9 can close:

- [ ] implement the optional Forge execution adapter;
- [ ] add source-level provenance for any actually copied/adapted code;
- [ ] pass applicable executor contract tests;
- [ ] add migration/regression tests;
- [ ] implement and test recovery/historical-event/idempotency integration;
- [ ] prove Forge can be disabled/removed while core/reference execution stays functional.
