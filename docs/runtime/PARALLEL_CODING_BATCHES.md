# Parallel coding batches

The parallel coding batch runtime adds a composition layer for parallel repository-oriented coding work. It does **not** replace the platform authorities it composes.

## Authority boundary

The batch layer owns only orchestration state: work-item readiness, conservative overlap classification, workstream provenance links, integration-candidate state, bounded repair/replay state, stale-base state and merge-readiness projection.

Existing authorities remain canonical:

- Planning owns Task-level decomposition and Plan revisions;
- Coordination owns durable Plan/Step readiness;
- Workspaces own Workspace/Snapshot identity, isolation, materialization and retention;
- Repository integration owns refs/revisions, branches, commits, diffs, provider checks and change requests;
- Agent Runtime owns Agent/AgentTeam revisions and AgentRun execution;
- Verification owns exact-subject evidence;
- Authorization owns Approval for repository side effects;
- Observability owns operational telemetry.

A provider-native branch, worktree path, CI check ID or PR ID is never a replacement for canonical Task/Step/Workspace/AgentRun identity.

## Workstream chain

Each workstream preserves the following correlation chain:

```text
Task
  -> Plan revision
    -> Step
      -> Agent revision / AgentRun
        -> Workspace / Snapshot
          -> Repository + exact base revision
            -> provider-local branch/ref
              -> output revision + diff digest/artifacts
                -> exact-subject Verification
```

The `coding_batches` package records that chain and rejects conflicting retry data instead of silently allocating a second logical workstream.

## Conservative concurrency

`ConservativeOverlapClassifier` distinguishes explicit dependency, exact affected-path/textual conflict, semantic ownership/impact overlap, proven independence and unknown evidence. Only proven independence is parallel-safe. Unknown evidence is serialized deterministically. Optional repository intelligence may enrich semantic scopes, but the baseline works from deterministic repository/path ownership hints without a hosted or paid service.

## Productive runtime composition

`CanonicalCodingWorkstreamDispatcher` is the supported dispatch bridge from canonical coding Steps into the canonical execution authorities. Before materialization it binds the exact durable Plan revision, then composes Agent execution with the existing Workspace/Snapshot and repository runtime boundaries. The coding-batch state machine records only the returned canonical identities and never substitutes its own execution authority.

A clean combined integration is also ordinary canonical work. `CanonicalCodingIntegrationDispatcher` accepts only a ready, non-stale and conflict-free `IntegrationCandidate`, binds it to an already active Integration Step, creates or reuses the exact Integration AgentRun, and records a dedicated `IntegrationExecutionProvenance`. `CanonicalIntegrationMaterializer` supplies the #37/#82 isolation boundary for the integration attempt: a deterministic isolated Workspace/Snapshot and provider-local integration branch are based on the candidate's exact target revision.

The candidate enters `INTEGRATING` before any combined SHA can be accepted. `reconcile_repository_output()` advances it to `VALIDATING` only after the bound Integration AgentRun has succeeded and repository integration exposes exact `RepositoryRunProvenance` for the same Task/Run, repository, target-base revision, Agent and non-empty diff-artifact evidence. Consequently, a caller cannot advance a clean candidate from `READY` by merely supplying an arbitrary Git SHA.

Repository outputs are projected into coding-batch validation only through `CanonicalRepositoryOutputVerifier`: the exact repository Run provenance and complete diff-artifact set must be covered by a completed passing Verification. Combined integration validation and repair outputs therefore cannot become merge-ready on a Git SHA alone.

The telemetry adapter emits batch/workstream/integration correlation using canonical IDs and revisions while keeping lifecycle truth in the owning subsystems.

## Integration and repair safety

Accepted workstreams still do not imply an accepted integration. An explicit `IntegrationCandidate` records ordered workstream revisions and blocks on overlapping output paths, unresolved semantic/unknown overlap, or a target branch revision different from the recorded base.

For a clean candidate, `IntegrationExecutionProvenance` records the canonical Integration Task, Plan revision, Step, Run, Agent revision/AgentRun, Workspace/Snapshot and provider-local integration branch. This provenance is restart-durable in the SQLite composition store and is exposed through the Control Plane read model.

`CodingBatchRepairCoordinator` binds a blocked candidate to an **already existing canonical `Plan`/`Step`**. It never creates a replacement Step type or scheduler. Repair attempts are deterministic, bounded, preserve their source conflicts/blockers and cannot reuse the same canonical repair Step identity.

A repaired/rebased output only advances when fresh exact-subject Verification passes for that repaired revision. It then returns to `VALIDATING`; prior combined validation is cleared, so combined tests and required checks must run again on the repaired SHA. If the target branch moves again, existing combined evidence is invalidated and the candidate returns to visible stale/blocked state. A target move during an active repair also terminates that repair attempt instead of silently continuing against a stale assumption.

The invariant is intentional:

> `A passes` + `B passes` does not imply `A+B passes`.

## Authorization boundary

`AuthorizedCodingBatchIntegration` composes the canonical `AuthorizationGate` before merge readiness. The `ProposedAction` digest binds the authorization/Approval decision to the exact batch, repository target, integration candidate, ordered workstream revisions, integrated revision and combined Verification identity. A policy requiring Approval therefore cannot be satisfied by an Approval for a different integration revision. Actual push/PR/merge side effects remain separately enforced by repository integration through the same canonical authorization boundary.

The supported `CodingBatchCoordinator.mark_merge_ready()` path fails closed. The internal state coordinator exposes no caller-supplied `authorization_granted` boolean; only the authorization adapter can invoke the private post-authorization transition after Authorization has enforced the exact action.

## Retry and recovery baseline

Batch creation is idempotent by caller-supplied request key plus a deterministic input fingerprint. Per-workstream branch names are deterministic from batch/work-item identity. Replaying the same materialization facts returns the existing state; conflicting Workspace/Snapshot/AgentRun facts fail closed.

Clean integration materialization follows the same rule. A candidate can bind only one exact `IntegrationExecutionProvenance`; an exact retry reuses it, while different Plan/Run/AgentRun/Workspace evidence fails closed. The Integration AgentRun is reconciled from Agent Runtime by its canonical Run rather than re-created blindly.

`CodingBatchStore` remains the persistence boundary. `InMemoryCodingBatchStore` is useful for tests and ephemeral reference composition, while `SqliteCodingBatchStore` provides a local/self-hosted restart-durable implementation without a paid service dependency. The SQLite store persists only coding-batch composition/provenance state; canonical Task, Plan, Step, Workspace, Repository, AgentRun, Verification and Approval resources remain owned by their existing subsystems and are referenced by identity rather than duplicated.

The durable payload includes clean integration execution provenance as well as repair attempts, original conflict evidence, canonical repair Plan/Step references, repaired output revision and repair Verification. Restart tests reconstruct a candidate while it is `INTEGRATING`, preserve the exact bound Run/AgentRun/Workspace evidence, and also prove idempotent replay of an already recorded repair result.

## Batch aggregation policy

The integration-candidate boundary enforces the configured batch semantics rather than storing them as display-only metadata:

- `all_required` refuses a partial batch;
- `best_effort` may integrate the verified accepted subset when unrelated siblings fail or are deferred;
- `dependency_closed` refuses a selected workstream unless its selected hard dependencies are included;
- `manual_selection` leaves the accepted candidate subset explicit while all ordinary acceptance/conflict/stale-base gates still apply.

## Control Plane

The versioned Control Plane exposes durable coding-batch resources through `coding-batches` and authenticated/idempotent command handlers for batch creation and integration-candidate creation. Clients receive workstream state, canonical Agent/Workspace/repository provenance, overlap/conflict decisions, clean integration execution provenance, repair attempts, combined validation/check state, blockers and change-request references without treating raw Git state as correctness authority.

## Implemented acceptance coverage

The implementation now includes:

- canonical batch/workstream/integration read state;
- conservative dependency/overlap classification and bounded parallel-ready projection;
- deterministic workstream and integration branch/workspace idempotency policy;
- exact Plan revision binding before workstream materialization;
- productive coordinator -> Agent Runtime -> Workspace/repository coding-workstream dispatch composition;
- exact workstream result and repository -> Verification binding;
- explicit integration candidate/conflict state and enforced aggregation policy;
- canonical clean-integration execution through Integration Step -> AgentRun -> isolated Workspace/repository integration materialization;
- exact repository integration-output reconciliation before `VALIDATING`;
- bounded canonical Plan/Step repair composition with fresh repaired-output Verification;
- stale-target replay/rebase invalidation with mandatory fresh revalidation;
- combined validation and stale-check rejection;
- exact-action authorization composition before merge readiness with no caller-asserted authorization boolean;
- restart-durable SQLite composition persistence, including `INTEGRATING` execution provenance and active/finished repair attempts;
- batch/workstream/integration telemetry using canonical correlation IDs;
- versioned Control Plane resources/commands including integration execution and repair provenance;
- dependency-cycle rejection and dependency-topological integration ordering;
- hard-dependency versus serialization-only failure semantics;
- deterministic evaluation coverage for safe parallelism and regressions;
- platform-conformance release scenario `Z`, including three coding workstreams plus a canonical fourth Integration Step and a conflicting-workstream repair path;
- focused coding-batch restart, authorization, verification, observability, orchestration and safety regression tests.

The platform-conformance E2E path proves that two independent coding Steps can run concurrently while a dependent Step waits for canonical predecessor acceptance. Once all accepted predecessor Steps complete, the coordinator activates a fourth Integration Step. The Integration AgentRun is bound to isolated integration provenance, repository evidence supplies the combined revision, and that revision must then pass fresh combined validation and exact authorization before merge readiness. A separate conflict scenario proves that overlapping outputs remain blocked until a bounded canonical repair produces a newly verified revision and the repaired integration passes fresh combined validation.

All productive side effects continue to invoke the existing Workspace/repository/Agent/Verification/Authorization authorities; the coding-batch runtime introduces no replacement lifecycle or repository authority.
