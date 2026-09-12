# Parallel coding batches

Issue #872 adds a composition layer for parallel repository-oriented coding work. It does **not** replace the platform authorities it composes.

## Authority boundary

The batch layer owns only orchestration state: work-item readiness, conservative overlap classification, workstream provenance links, integration-candidate state, bounded repair/replay state, stale-base state and merge-readiness projection.

Existing authorities remain canonical:

- #439 owns Task-level planning/decomposition and Plan revisions;
- #384 owns durable Plan/Step coordination and readiness;
- #37 owns Workspace/Snapshot identity, isolation, materialization and retention;
- #82 owns repository refs/revisions, branches, commits, diffs, provider checks and change requests;
- #33 owns Agent/AgentTeam revisions and AgentRun execution;
- #86 owns exact-subject Verification evidence;
- #15 owns authorization/Approval for repository side effects;
- #16 owns operational observability.

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

`ConservativeOverlapClassifier` distinguishes explicit dependency, exact affected-path/textual conflict, semantic ownership/impact overlap, proven independence and unknown evidence. Only proven independence is parallel-safe. Unknown evidence is serialized deterministically. Optional repository intelligence (#502) may enrich semantic scopes, but the baseline works from deterministic repository/path ownership hints without a hosted or paid service.

## Productive runtime composition

`CanonicalCodingBatchDispatcher` is the supported dispatch bridge from #384 coding Steps into the canonical execution authorities. Before materialization it binds the exact durable Plan revision, then composes #33 Agent execution with the existing #37 Workspace/Snapshot and #82 repository runtime boundaries. The #872 state machine records only the returned canonical identities and never substitutes its own execution authority.

Repository outputs are projected into #872 only through `CanonicalRepositoryOutputVerifier`: the exact #82 Run provenance and complete diff-artifact set must be covered by a completed passing #86 Verification. Combined integration validation and repair outputs therefore cannot advance on a Git SHA alone.

The #16 telemetry adapter emits batch/workstream/integration correlation using canonical IDs and revisions while keeping lifecycle truth in the owning subsystems.

## Integration and repair safety

Accepted workstreams still do not imply an accepted integration. An explicit `IntegrationCandidate` records ordered workstream revisions and blocks on overlapping output paths, unresolved semantic/unknown overlap, or a target branch revision different from the recorded base.

`CodingBatchRepairCoordinator` binds a blocked candidate to an **already existing canonical #439/#384 `Plan`/`Step`**. It never creates a replacement Step type or scheduler. Repair attempts are deterministic, bounded, preserve their source conflicts/blockers and cannot reuse the same canonical repair Step identity.

A repaired/rebased output only advances when fresh exact-subject Verification passes for that repaired revision. It then returns to `VALIDATING`; prior combined validation is cleared, so combined tests and required checks must run again on the repaired SHA. If the target branch moves again, existing combined evidence is invalidated and the candidate returns to visible stale/blocked state. A target move during an active repair also terminates that repair attempt instead of silently continuing against a stale assumption.

The invariant is intentional:

> `A passes` + `B passes` does not imply `A+B passes`.

## Authorization boundary

`AuthorizedCodingBatchIntegration` composes the #15 `AuthorizationGate` before merge readiness. The `ProposedAction` digest binds the authorization/Approval decision to the exact batch, repository target, integration candidate, ordered workstream revisions, integrated revision and combined Verification identity. A policy requiring Approval therefore cannot be satisfied by an Approval for a different integration revision. Actual push/PR/merge side effects remain separately enforced by #82 through the same canonical #15 boundary.

The supported `CodingBatchCoordinator.mark_merge_ready()` path fails closed. The internal state coordinator exposes no caller-supplied `authorization_granted` boolean; only the authorization adapter can invoke the private post-authorization transition after #15 has enforced the exact action.

## Retry and recovery baseline

Batch creation is idempotent by caller-supplied request key plus a deterministic input fingerprint. Per-workstream branch names are deterministic from batch/work-item identity. Replaying the same materialization facts returns the existing state; conflicting Workspace/Snapshot/AgentRun facts fail closed.

`CodingBatchStore` remains the persistence boundary. `InMemoryCodingBatchStore` is useful for tests and ephemeral reference composition, while `SqliteCodingBatchStore` provides a local/self-hosted restart-durable implementation without a paid service dependency. The SQLite store persists only #872 composition/provenance state; canonical Task, Plan, Step, Workspace, Repository, AgentRun, Verification and Approval resources remain owned by their existing subsystems and are referenced by identity rather than duplicated.

The durable payload includes repair attempts, original conflict evidence, canonical repair Plan/Step references, repaired output revision and repair Verification. Restart tests reconstruct active repair state and prove idempotent replay of an already recorded repair result.

## Implemented acceptance coverage

The #872 implementation now includes:

- canonical batch/workstream/integration read state;
- conservative dependency/overlap classification and bounded parallel-ready projection;
- deterministic branch-ref/idempotency policy;
- exact #439/#384 Plan revision binding before materialization;
- productive #384 -> #33 -> #37/#82 dispatch composition;
- exact workstream result and #82 -> #86 Verification binding;
- explicit integration candidate/conflict state;
- bounded canonical Plan/Step repair composition with fresh repaired-output Verification;
- stale-target replay/rebase invalidation with mandatory fresh revalidation;
- combined validation and stale-check rejection;
- exact-action #15 authorization composition before merge readiness with no caller-asserted authorization boolean;
- restart-durable SQLite composition persistence, including active/finished repair attempts;
- #16 batch/workstream/integration telemetry using canonical correlation IDs;
- Control Plane-safe read projection including repair provenance;
- dependency-cycle rejection and dependency-topological integration ordering;
- hard-dependency versus serialization-only failure semantics;
- #19 deterministic evaluation coverage for safe parallelism and regressions;
- #46 release conformance scenario `Z`, including three-work-item fan-out/fan-in and a conflicting-workstream repair path;
- focused #872 restart, authorization, verification, observability, orchestration and safety regression tests.

The #46 E2E path proves that two independent coding Steps can run concurrently while a dependent Step waits for canonical predecessor acceptance; the integrated revision must then pass fresh combined validation and exact #15 authorization. A separate conflict scenario proves that overlapping outputs remain blocked until a bounded canonical repair produces a newly verified revision and the repaired integration passes fresh combined validation.

All productive side effects continue to invoke the existing #37/#82/#33/#86/#15 authorities; #872 introduces no replacement lifecycle or repository authority.
