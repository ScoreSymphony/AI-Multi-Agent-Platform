# Backend Responsibility Map (#723)

Status: implementation and review guide for issue #723. This document records responsibility boundaries and the completed decomposition behind stable façades. It is intentionally about ownership and coupling rather than line-count targets.

## Rules for this refactor

1. Public package façades and existing contracts remain stable unless a separate issue explicitly changes them.
2. Canonical ownership must not move as a side effect of file extraction.
3. New modules must have one named responsibility; no generic `utils.py`, `helpers.py`, or catch-all `common.py` modules.
4. Internal services may depend on narrow protocols, but issue #723 must not reopen private kernel command primitives to external callers. The encapsulation established by #567 remains authoritative.
5. Restart, retry, idempotency, ordering, cancellation, authorization, and fail-closed behavior are invariants, not implementation details.
6. Semantic/package naming cleanup is intentionally deferred to dedicated follow-up work, including #727, once boundaries are stable.
7. File size is diagnostic only. A large cohesive module may remain large; a smaller module with unrelated ownership must still be split.

## Hotspot map

| Hotspot | Responsibilities audited | Focused internal boundaries | Stable façade / ownership preserved | Primary regression focus | Status in #723 |
| --- | --- | --- | --- | --- | --- |
| `kernel/kernel.py` | Task commands; Run commands; reads; active-run selection; lifecycle dispatch/reconciliation; recovery; event/command commits; completion integration | `queries.py`, `recovery.py`, `task_commands.py`, `run_commands.py`, `lifecycle.py`, `commit_support.py` | public `PlatformKernel`; kernel remains canonical Task/Run/Event lifecycle authority; `OutputObservingPlatformKernel` remains compatible | lifecycle transitions, idempotency, event history, cancellation races, recovery/restart, completion verification | **implemented** |
| `distributed/workspace_transport.py` | worker materialization; chunk staging/commit; result collection; path/symlink safety; control client; worker endpoint; workspace-bound routing; wire codecs/checksums | materialization store, wire codec/contract, remote materializer, worker endpoint, workspace-bound worker | `RemoteWorkspaceMaterializer` contract; canonical Workspace/Snapshot/File identity stays control-plane owned; worker paths remain local deployment detail | interrupted transfers, duplicate chunks, checksum failure, cache replay, result collection, cleanup, read-only enforcement, request/reply correlation | **implemented** |
| `coordination/service.py` | Plan registration; graph validation; dependency barriers; Run attempts; outcome observation; retries; durable waits; cancellation; restart reconciliation; task aggregation; claims; telemetry | `registration.py`, `progression.py`, `attempt_outcomes.py`, `waits.py`, `cancellation.py`, `reconciliation.py`, `aggregation.py` | `DurablePlanStepCoordinator`; kernel owns canonical Run/Task truth; repository owns durable coordination projection | claim races, duplicate observations, retry due-times, waits, cancellation, restart/reconcile, predecessor failure, aggregate completion | **implemented** |
| `data/reference.py` | shared SQLite primitives plus File, Memory and Knowledge provider implementations | `reference_support.py`, `reference_file.py`, `reference_memory.py`, `reference_knowledge.py` | `FileProvider`, `MemoryProvider`, `KnowledgeProvider` contracts and existing `data.reference` import surface | restart persistence, scope isolation, tombstones/orphans, expiry/supersession, knowledge revisions/search | **implemented** |
| `planning/service.py` | trigger/idempotency handling; inventory; proposal construction/validation; authorization/approval; activation; prior-plan reconstruction; bounded replanning; coordinator handoff | `inventory.py`, `validation.py`, `replanning.py`, `proposals.py`, `handoff.py`, `activation.py`; specialized supersession/composition layers remain above the base façade | `PlanningService`; planning never executes Steps or creates Runs; coordinator receives the canonical Plan/Step graph | deterministic validation, stale proposal rejection, approval binding, bounded replanning, restart activation recovery, exact handoff | **implemented** |
| `security/authentication.py` | auth records; password hashing; identity/session/credential store; brute-force/replay protection; local password flows; sessions/CSRF; bearer/worker auth; external identities; audit; serializers; tokens | `authentication_models.py`, `authentication_passwords.py`, `authentication_store.py`, `authentication_protection.py`, `authentication_accounts.py`, `authentication_sessions.py`, `authentication_credentials.py`, `authentication_external.py`, `authentication_audit.py`, `authentication_serialization.py`, `authentication_tokens.py` | `LocalAuthenticationService`; authorization remains separate; failures remain fail-closed and secrets non-retrievable/redacted | disabled/locked accounts, timing-safe password path, expiry/revocation/CSRF, worker replay, external mapping, redaction | **implemented** |
| `control_plane/service.py` | scope identity; health; Project/Workspace APIs; Task/Run APIs; reference projections; timeline/subscription; model/provider APIs; authorization; validation/serialization | `scope_store.py`, `scope_service.py`, `task_run_service.py`, `reference_event_service.py`, `model_registry_service.py`, `health.py`, `authorization_service.py`, `request_validation.py`, `resources.py` | `ControlPlane` northbound façade and existing API contracts; kernel/domain/provider ownership unchanged | API snapshots, idempotency, authorization allow/deny/list filtering, event cursors, provider health, model enable/disable | **implemented** |

## Implemented cohorts

### Cohort 1 — Kernel read/recovery extraction

- `kernel/queries.py` owns Task/Run/Event reads, Run ownership validation, active-run selection and attempt lookup.
- `kernel/recovery.py` owns restart/reconciliation iteration through a narrow internal host protocol.
- `PlatformKernel` retains the supported façade and delegates those mechanics.
- Repository/private command primitives remain internal to the kernel package.

### Cohort 2 — Data reference provider decomposition

- `data/reference_file.py` owns filesystem-backed File persistence and lifecycle behavior.
- `data/reference_memory.py` owns scoped Memory persistence, expiry and supersession.
- `data/reference_knowledge.py` owns Knowledge source/document/index persistence and retrieval.
- `data/reference_support.py` contains only genuinely shared SQLite/serialization/access primitives.
- `data/reference.py` remains a behavior-free compatibility façade.

### Cohort 3 — Kernel Task command extraction

- `kernel/task_commands.py` owns Task create/update/ready/wait/resume/complete/fail/cancel command handling.
- `PlatformKernel` preserves public method names/signatures and delegates.
- Task cancellation still routes active Runs through the canonical Run cancellation path.
- Completion verification and canonical commit semantics are unchanged.

### Cohort 4 — Kernel Run command extraction

- `kernel/run_commands.py` owns Run create/retry/start/refresh/outcome/cancel plus artifact/result attachment orchestration.
- It uses narrow kernel capabilities rather than creating its own lifecycle authority.
- Task cancellation and retries still converge on canonical kernel Run semantics.

### Cohort 5 — Kernel lifecycle reconciliation extraction

- `kernel/lifecycle.py` owns backend dispatch, backend snapshot reconciliation, cancellation completion, recovery markers and terminal transition construction.
- Canonical Task/Run state remains event-sourced through the existing kernel repository and commit path.
- Completion verification remains part of terminal transition construction.

### Cohort 6 — Kernel canonical commit support extraction

- `kernel/commit_support.py` owns idempotency lookup/validation, canonical event/command construction, optimistic commits, operation contexts and event-sink mirroring.
- Domain command components still choose transitions; commit support does not become a domain authority.
- Compatibility wrappers in `PlatformKernel` preserve the existing private internal capability surface.

### Cohort 7 — Workspace materialization state and wire codec extraction

- `distributed/workspace_materialization_store.py` owns worker-local transfer/materialization state, path safety, read-only enforcement, result scanning and cleanup.
- `distributed/workspace_transport_codec.py` owns manifest/wire serialization, checksums and validation.
- Canonical Workspace/Snapshot/File identity remains control-plane owned.

### Cohort 8 — Workspace client, endpoint and routing extraction

- `distributed/workspace_remote_materializer.py` owns the control-side transport client.
- `distributed/workspace_transport_endpoint.py` owns worker-side transfer command handling.
- `distributed/workspace_bound_worker.py` owns workspace-bound Worker routing/composition.
- `distributed/workspace_transport_contract.py` owns transport topics and schema constants.
- `distributed/workspace_transport.py` remains a compatibility façade.

### Cohort 9 — Coordination registration and graph validation

- `coordination/registration.py` owns Step-graph validation, canonical Task/Plan identity checks, retry-policy reference validation and initial durable records.
- `register_plan` delegates registration mechanics while the façade retains telemetry, initial progression and projection.
- `_validate_graph` remains a thin compatibility shim for inherited internal callers.

### Cohort 10 — Coordination progression, waits, outcomes and recovery

- `coordination/progression.py` owns dependency refresh and Run-attempt dispatch.
- `coordination/attempt_outcomes.py` owns canonical Run outcome observation, retry decisions and retry activation.
- `coordination/waits.py` owns durable wait validation, persistence and resolution.
- `coordination/cancellation.py` owns Plan and active-Run cancellation propagation.
- `coordination/aggregation.py` owns terminal Step-to-Task aggregation.
- `coordination/reconciliation.py` owns restart reconciliation against canonical kernel Run truth and inconsistent-state marking.
- `DurablePlanStepCoordinator` keeps orchestration, claims, telemetry and projection while delegating responsibility-specific mechanics.

Claims and expected-revision writes remain repository-governed throughout the split.

### Cohort 11 — Planning decomposition

- `planning/inventory.py` owns sanitized, scope-compatible inventory construction.
- `planning/validation.py` owns deterministic proposal, graph, parallelism, model, capability and provider-metadata validation.
- `planning/replanning.py` owns prior-plan lookup, trigger fingerprints and bounded replan policy.
- `planning/proposals.py` owns immutable proposal construction.
- `planning/handoff.py` owns canonical activated-plan reconstruction and coordinator handoff.
- `planning/activation.py` owns activation authorization/approval and canonical Plan commit mechanics.
- The public/base `PlanningService` retains proposal-history/orchestration behavior and delegates focused mechanics; supersession/composition layers remain layered above those seams.

Planning remains incapable of Step execution or Worker dispatch.

### Cohort 12 — Authentication decomposition

- Domain/result records, password hashing, storage, brute-force/replay protection, accounts, sessions, credentials, external identities, audit, serialization and tokens now live in focused authentication modules.
- `LocalAuthenticationService` delegates account, session, credential/worker and external-identity entrypoints to those services.
- Security-sensitive hashing/comparison and secret-generation mechanics are not reimplemented in the façade.
- Fail-closed account state, expiry/revocation, CSRF, replay rejection and redaction semantics remain in focused security components.

### Cohort 13 — Control Plane decomposition

- `scope_store.py` owns Project/Workspace identity storage.
- `scope_service.py` owns Project/Workspace northbound operations and their authorization/scoping mechanics.
- `task_run_service.py` owns Task/Run northbound lifecycle calls while `PlatformKernel` remains canonical lifecycle authority.
- `reference_event_service.py` owns reference projections, timeline reads and Task event subscriptions.
- `model_registry_service.py` owns model/provider northbound projections and registry operations.
- `health.py` owns health aggregation.
- `authorization_service.py` owns authorization request construction/checking and payload binding.
- `request_validation.py` and `resources.py` own request parsing and northbound resource serialization respectively.
- `ControlPlane` retains method signatures, injected dependencies and private compatibility seams used by existing extension layers while delegating responsibility-specific mechanics.

## Architecture guards

Architecture tests enforce the decomposition rather than relying on convention alone:

- focused kernel components cannot depend back on `PlatformKernel`;
- focused workspace transport components cannot depend back on `workspace_transport.py`;
- File/Memory/Knowledge implementations cannot move back into `data.reference`;
- focused coordination components cannot depend back on `coordination.service` and façade entrypoints must delegate to the correct component;
- focused planning components cannot depend back on the planning service/supersession façades;
- focused authentication components cannot depend back on `security.authentication`, and security-sensitive primitives cannot be reintroduced there;
- focused Control Plane components cannot depend back on `control_plane.service`, and northbound scope, Task/Run, references/events, model, health and authorization operations must remain delegated.

## Integration / parallel-branch policy

The #723 branch starts from `main` and must stay free of unrelated issue changes. Each cohort is kept as responsibility-focused commits so it can be reviewed and merged onto `main` once the complete branch passes CI.

When another active issue touches one of these hotspot files:

- do not merge that issue into #723 merely to reduce local conflicts;
- rebase/merge accepted `main` prerequisites only when necessary;
- resolve semantic/package naming work after responsibility boundaries are stable;
- preserve compatibility shims at package façades when extraction would otherwise force broad downstream edits.

## Acceptance evidence required before #723 can close

- all seven hotspot audits above are reflected in code;
- `PlatformKernel` no longer directly implements every Task command, Run command, query, recovery, lifecycle and commit-support concern in one object/file;
- Control Plane northbound behavior and authorization decisions remain unchanged;
- authentication remains fail-closed and secret-safe;
- File/Memory/Knowledge persistence and scope behavior survive restart/regression coverage;
- distributed Workspace transfer and coordination retain retry/restart/order/cancel/concurrency behavior;
- planning activation/recovery and bounded replanning remain deterministic;
- compatibility-private imports used by existing internal extension layers continue to resolve;
- Ruff formatting/lint, static typing, regression tests and the full required CI suite pass;
- architecture guards prevent the same responsibilities from silently reconverging into catch-all modules.
