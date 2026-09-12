# Parallel coding batches

Issue #872 adds a composition layer for parallel repository-oriented coding work. It does **not** replace the platform authorities it composes.

## Authority boundary

The batch layer owns only orchestration state: work-item readiness, conservative overlap classification, workstream provenance links, integration-candidate state, stale-base state and merge-readiness projection.

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

The current `coding_batches` package records that chain and rejects conflicting retry data instead of silently allocating a second logical workstream.

## Conservative concurrency

`ConservativeOverlapClassifier` distinguishes:

- explicit dependency;
- exact affected-path/textual conflict;
- semantic ownership/impact overlap;
- proven independence;
- unknown evidence.

Only proven independence is parallel-safe. Unknown evidence is serialized deterministically. Optional repository intelligence (#502) may enrich semantic scopes, but the baseline works from deterministic repository/path ownership hints without a hosted or paid service.

## Integration safety

Accepted workstreams still do not imply an accepted integration. An explicit `IntegrationCandidate` records ordered workstream revisions and blocks on:

- overlapping output paths;
- unresolved semantic/unknown overlap;
- a target branch revision different from the recorded batch base.

A successful combined revision then requires fresh combined tests/Verification and required checks bound to that exact revision. Passing checks for an older SHA are rejected.

`AuthorizedCodingBatchIntegration` composes the #15 `AuthorizationGate` before merge readiness. The `ProposedAction` digest binds the authorization/Approval decision to the exact batch, repository target, integration candidate, ordered workstream revisions, integrated revision and combined Verification identity. A policy requiring Approval therefore cannot be satisfied by an Approval for a different integration revision. Actual push/PR/merge side effects remain separately enforced by #82 through the same canonical #15 boundary.

The invariant is intentional:

> `A passes` + `B passes` does not imply `A+B passes`.

## Retry and recovery baseline

Batch creation is idempotent by caller-supplied request key plus a deterministic input fingerprint. Per-workstream branch names are deterministic from batch/work-item identity. Replaying the same materialization facts returns the existing state; conflicting Workspace/Snapshot/AgentRun facts fail closed.

`CodingBatchStore` remains the persistence boundary. `InMemoryCodingBatchStore` is useful for tests and ephemeral reference composition, while `SqliteCodingBatchStore` provides a local/self-hosted restart-durable implementation without a paid service dependency. The SQLite store persists only #872 composition/provenance state; canonical Task, Plan, Step, Workspace, Repository, AgentRun, Verification and Approval resources remain owned by their existing subsystems and are referenced by identity rather than duplicated.

Restart tests reconstruct a fresh `CodingBatchCoordinator` from the SQLite database after produced work, then continue Verification/integration, restart again and recover combined validation/check evidence without allocating a second logical batch.

## Current implementation slice

The current #872 slice contains:

- canonical batch/workstream/integration read state;
- conservative dependency/overlap classification;
- bounded parallel-ready projection;
- deterministic branch-ref/idempotency policy;
- exact workstream result and Verification binding;
- explicit integration candidate/conflict state;
- stale-target blocking;
- combined validation and stale-check rejection;
- exact-action #15 authorization composition before merge readiness;
- restart-durable SQLite composition persistence plus in-memory reference storage;
- Control Plane-safe read projection;
- dependency-cycle rejection and dependency-topological integration ordering;
- hard-dependency versus serialization-only failure semantics;
- focused #872 safety/regression tests.

## Remaining acceptance work

The issue is not complete yet. Remaining work includes:

- explicit bounded Integration/Repair Step composition through #439/#384 rather than a replacement workflow authority;
- controlled stale-base replay/rebase and mandatory fresh revalidation;
- sealing/removing the low-level boolean merge-readiness seam now that the production composition path has exact #15 action binding;
- fuller productive composition with #33 AgentRun, #86 Verification and #16 observability boundaries;
- the required #19 evaluation fixtures and #46 multi-workstream/conflict E2E conformance scenarios.

Those follow-up paths must continue to invoke the existing #37/#82/#33/#86/#15 authorities for actual side effects; no new authority should be introduced to accomplish the wiring.
