# Backend Responsibility Map (#723)

Status: implementation guide for issue #723. This document records responsibility boundaries before and during decomposition. It is intentionally about ownership and coupling rather than line-count targets.

## Rules for this refactor

1. Public package façades and existing contracts remain stable unless a separate issue explicitly changes them.
2. Canonical ownership must not move as a side effect of file extraction.
3. New modules must have one named responsibility; no generic `utils.py`, `helpers.py`, or catch-all `common.py` modules.
4. Internal services may depend on narrow protocols, but issue #723 must not reopen private kernel command primitives to external callers. The encapsulation established by #567 remains authoritative.
5. Restart, retry, idempotency, ordering, cancellation, authorization, and fail-closed behavior are invariants, not implementation details.
6. Semantic/package naming cleanup is intentionally deferred to the dedicated follow-up work (including #727) once boundaries are stable.
7. File size is diagnostic only. A large cohesive module may remain large; a smaller module with unrelated ownership must still be split.

## Hotspot map

| Hotspot | Responsibilities currently combined | Target internal boundaries | Stable façade / ownership to preserve | Primary regression focus | Status in #723 |
| --- | --- | --- | --- | --- | --- |
| `kernel/kernel.py` | Task commands; Run commands; Task/Run/Event reads; active-run selection; lifecycle dispatch/reconciliation; recovery; event/command commit mechanics; completion integration | query/read service; recovery coordinator; Task command service; Run command service; lifecycle reconciler; canonical event/command commit support | public `PlatformKernel`; kernel remains canonical Task/Run/Event lifecycle authority; `OutputObservingPlatformKernel` remains compatible | lifecycle transitions, idempotency, event history, cancellation races, recovery/restart, completion verification | **in progress**: queries, recovery, Task commands and Run commands extracted behind the façade |
| `distributed/workspace_transport.py` | Worker-local materialization state; chunk staging/commit; result collection; path/symlink safety; control-side transport client; worker-side transport endpoint; workspace-bound worker routing; wire codecs/checksums | materialization store; control-side remote materializer; worker endpoint; workspace-bound dispatcher; workspace wire codec | `RemoteWorkspaceMaterializer` contract; canonical Workspace/Snapshot/File identity remains control-plane owned; worker paths remain local deployment detail | interrupted transfers, duplicate chunks, checksum failure, cache replay, result collection, cleanup, read-only enforcement, request/reply correlation | audited; split pending |
| `coordination/service.py` | Plan registration/graph validation; dependency barriers; Run-attempt creation/dispatch; Run outcome observation; retry scheduling; durable waits and resolution; cancellation; restart reconciliation; task aggregation; claims; telemetry | graph/registration validator; progression engine; attempt/retry coordinator; wait coordinator; recovery/reconciliation coordinator; task aggregation; telemetry adapter | `DurablePlanStepCoordinator` façade; kernel owns canonical Run/Task truth; repository owns durable coordination projection | contention/claim races, duplicate observations, retry due-times, waits, cancellation, restart/reconcile, predecessor failure, aggregate completion | audited; split pending |
| `data/reference.py` | SQLite helpers plus three independent reference providers: File, Memory, Knowledge; each includes schema initialization, persistence mapping, scope checks and provider compatibility methods | shared SQLite connection/serialization primitives only where semantically shared; `LocalFileProvider`; `LocalMemoryProvider`; `LocalKnowledgeProvider` in dedicated modules | `FileProvider`, `MemoryProvider`, `KnowledgeProvider` contracts and current public exports | persistence restart, scope isolation, tombstones/orphans, memory expiry/supersession, knowledge revisions/index status/search | **implemented**: provider implementations split; `data.reference` retained as compatibility façade |
| `planning/service.py` | trigger/idempotency handling; prior-plan reconstruction; inventory construction; planner invocation; proposal construction; structural/capability/model validation; authorization/approval; activation; canonical event provenance lookup; coordinator handoff; bounded replanning; telemetry | proposal service façade; inventory builder; proposal validator; activation/authorization service; canonical plan handoff/reconstruction; prior-plan/replan policy support | `PlanningService`; planning never executes Steps or creates Runs; coordinator receives canonical Plan/Step graph only | deterministic validation, stale proposal rejection, approval binding, bounded replanning, restart activation recovery, exact coordinator handoff | audited; split pending |
| `security/authentication.py` | auth domain records/enums; password hashing; in-memory identity/session/credential store; brute-force limiter; replay protection; local user/password flows; browser sessions/CSRF; bearer credentials; worker authentication; external identity mapping; audit/safe serializers; token codecs | auth models/contracts; password hashing; store; rate limiting/replay protection; session service; credential service; external identity service; façade orchestration; safe serialization/token codec | `LocalAuthenticationService` and authentication result contracts; authorization remains separate; all failures remain fail-closed and secrets remain non-retrievable/redacted | invalid/disabled/locked accounts, password timing path, session expiry/revocation/CSRF, credential expiry/revocation, worker replay, external identity mapping, secret redaction | audited; split pending |
| `control_plane/service.py` | Project/Workspace identity storage; health aggregation; project/workspace API operations; Task/Run API operations; reference projections; timeline/subscription; model/provider API operations; authorization construction/checks; northbound serialization | scope store; health service; project/workspace resource service; Task/Run service; reference/timeline service; model registry service; shared authorization boundary; resource serializers | `ControlPlane` northbound façade and existing API contracts; kernel/domain/provider ownership unchanged | API contract snapshots, idempotency keys, authorization allow/deny/list filtering, event cursors, provider health, model enable/disable | audited; split pending |

## Cohort 1 — Kernel read/recovery extraction

Issue branch: `refactor/723-backend-decomposition`.

The first implementation cohort moves implementation responsibility without changing the supported façade:

- `kernel/queries.py` owns Task/Run/Event reads, Run ownership validation, active-run selection and attempt lookup.
- `kernel/recovery.py` owns restart/reconciliation iteration through a narrow internal host protocol.
- `kernel/kernel.py` keeps the public methods and delegates to those internal components.
- Canonical event persistence, Task/Run mutation authority and lifecycle ownership remain with the kernel.

This cohort deliberately does **not** expose repository or private command primitives outside the kernel package.

## Cohort 2 — Data reference provider decomposition

The second implementation cohort removes the independent File, Memory and Knowledge implementations from the former `data/reference.py` implementation monolith without changing provider contracts or supported imports:

- `data/reference_file.py` owns `LocalFileProvider`, filesystem persistence, file metadata, checksums, tombstones, artifact links and orphan detection.
- `data/reference_memory.py` owns the base `LocalMemoryProvider`, scoped-memory persistence, expiry, supersession and compatibility methods.
- `data/reference_knowledge.py` owns the base `LocalKnowledgeProvider`, source/document/index persistence and deterministic keyword retrieval.
- `data/reference_support.py` contains only genuinely shared SQLite connection, JSON/time serialization and access-context/error primitives.
- `data/reference.py` remains a behavior-free compatibility façade re-exporting the three base providers, so existing imports and `reference_lifecycle.py` compatibility remain intact.

Architecture tests require the compatibility façade to remain implementation-free and focused provider modules not to depend back on it. Persistence schemas, provider IDs, canonical identifiers and lifecycle-wrapper inheritance remain unchanged.

## Cohort 3 — Kernel Task command extraction

The third implementation cohort moves canonical Task mutation mechanics out of the public kernel façade without changing its supported API:

- `kernel/task_commands.py` owns Task creation plus update, ready, wait, resume, complete, fail and cancel command handling.
- `PlatformKernel` retains the existing public method names, signatures and return types and delegates those calls to `KernelTaskCommands`.
- `KernelTaskCommands` depends on a narrow internal host protocol rather than importing the concrete `PlatformKernel` façade.
- Task cancellation still routes active Runs through the canonical Run-cancellation path; the extraction does not create a second lifecycle authority.
- Completion verification still uses the existing completion authority and existing event construction/commit machinery.
- Task creation preserves its dedicated idempotency scope, canonical event format and repository commit semantics.

Architecture tests require every extracted Task mutation to remain delegated and prevent the focused component from depending back on `PlatformKernel`.

## Cohort 4 — Kernel Run command extraction

The fourth implementation cohort moves canonical Run command orchestration and output attachment commands out of the public kernel façade:

- `kernel/run_commands.py` owns Run creation/retry/start/refresh/outcome/cancel plus Task convenience start and artifact/result attachment commands.
- `PlatformKernel` retains the existing public method names and signatures and delegates them to `KernelRunCommands`.
- The focused component uses the existing query, lifecycle-reconciliation and commit capabilities through a narrow internal host protocol; it does not own a second repository or lifecycle truth.
- Task cancellation continues to call the public canonical Run cancellation path, which now delegates into the same focused Run command component.
- Lifecycle dispatch, snapshot reconciliation, terminal transition construction and commit/idempotency primitives remain separate follow-up boundaries rather than being duplicated into the command component.

Architecture tests require the extracted Run/output methods to stay delegated and prevent `run_commands.py` from depending on the concrete `PlatformKernel` façade.

## Planned implementation cohorts

### Kernel

1. **Read/recovery** — query mechanics and restart recovery. *(implemented)*
2. **Task commands** — create/update/ready/wait/resume/complete/fail/cancel behind an internal command component. *(implemented)*
3. **Run commands** — create/retry/start/refresh/outcome/cancel and output attachment orchestration. *(implemented)*
4. **Lifecycle reconciliation** — backend dispatch, snapshot reconciliation and cancellation completion.
5. **Commit support** — command/idempotency/event construction and mirroring, kept internal to the kernel package.

The order is chosen so each extraction can be reviewed against the same `PlatformKernel` façade and existing tests.

### Distributed workspace transport

1. Extract worker-local `WorkerWorkspaceMaterializationStore` and its filesystem safety/state persistence.
2. Extract transport-specific request/response codecs and checksums into a workspace-specific wire module.
3. Extract `TransportRemoteWorkspaceMaterializer` as the control-side transport client.
4. Extract `WorkerWorkspaceTransportEndpoint` as the worker-side command endpoint.
5. Extract `WorkspaceBoundLocalWorker` routing from transfer mechanics.

Do not move canonical Workspace/Snapshot/File ownership to Workers while performing these changes.

### Coordination

1. Extract graph validation/registration.
2. Extract dependency-barrier progression and attempt dispatch.
3. Extract retry scheduling plus durable wait resolution.
4. Extract recovery/reconciliation and cancellation mechanics.
5. Keep `DurablePlanStepCoordinator` as orchestration façade delegating to the focused components.

Claims and expected-revision writes remain repository-governed throughout the split.

### Data reference implementations

This cohort is implemented. The three provider implementations now live in focused modules and preserve `data.reference` as the compatibility façade. Shared support is intentionally limited to SQLite connection, serialization, access-context and common provider error primitives; provider-specific schema, scope and lifecycle rules stay with their canonical provider implementation.

### Planning

1. Extract sanitized inventory construction.
2. Extract deterministic proposal validation and graph/parallelism/model/capability checks.
3. Extract prior-plan reconstruction and bounded replanning policy checks.
4. Extract activation authorization/approval handling.
5. Extract canonical `plan.created` reconstruction and coordinator handoff.
6. Keep `PlanningService` as the public proposal/activation façade.

Planning must remain incapable of Step execution/Worker dispatch after decomposition.

### Authentication

1. Separate domain/result records and protocol contracts from implementations.
2. Separate password hashing, rate limiting and replay protection.
3. Separate session lifecycle/CSRF handling from long-lived credentials.
4. Separate external identity mapping/authentication.
5. Keep `LocalAuthenticationService` as a compatibility façade over these focused services.

No extraction may weaken constant-time secret comparison paths, revocation/expiry checks, CSRF validation, replay rejection, account-state enforcement or redaction.

### Control Plane

1. Move `ScopeStore` out of the application façade.
2. Group Project/Workspace operations behind one focused service.
3. Group Task/Run operations behind one focused service.
4. Group references/timeline/event subscription behind one focused service.
5. Group model/provider operations behind one focused service.
6. Centralize authorization request construction as an internal boundary without changing authorization semantics.
7. Keep `ControlPlane` as the stable northbound façade.

## Integration / parallel-branch policy

The #723 branch starts from `main` and must stay free of unrelated issue changes. Each cohort should be a small set of commits that can be merged or cherry-picked onto a later shared integration branch.

When another active issue touches one of these hotspot files:

- do not merge that issue into #723 merely to reduce local conflicts;
- finish the #723 cohort against its current base or rebase/merge `main` only when the prerequisite change is itself accepted;
- resolve semantic/package naming work after responsibility boundaries are stable;
- preserve compatibility shims at the package façade when file moves would otherwise force broad downstream edits.

## Acceptance evidence expected before #723 can close

- all seven hotspot audits above are reflected in code or explicitly justified as cohesive;
- `PlatformKernel` no longer directly implements every Task command, Run command, query, recovery and lifecycle concern in one object/file;
- Control Plane northbound behavior and authorization decisions are unchanged;
- authentication remains fail-closed and secret-safe;
- File/Memory/Knowledge persistence and scope behavior survives restart tests;
- distributed workspace transfer and coordination retain retry/restart/order/cancel/concurrency behavior;
- planning activation/recovery and bounded replanning remain deterministic;
- import/static-type checks and full CI pass;
- an architecture guard prevents the same responsibilities from silently reconverging into a new catch-all module.
