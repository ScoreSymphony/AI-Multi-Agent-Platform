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

A successful combined revision then requires fresh combined tests/Verification and required checks bound to that exact revision. Passing checks for an older SHA are rejected. #15 authorization is required before the candidate can become merge-ready.

The invariant is intentional:

> `A passes` + `B passes` does not imply `A+B passes`.

## Retry and recovery baseline

Batch creation is idempotent by caller-supplied request key plus a deterministic input fingerprint. Per-workstream branch names are deterministic from batch/work-item identity. Replaying the same materialization facts returns the existing state; conflicting Workspace/Snapshot/AgentRun facts fail closed.

The in-memory store is the reference implementation. Production persistence should implement `CodingBatchStore` over the existing platform persistence boundary so process restart can reload the same state rather than allocate new Workspaces, branches or integration attempts.

## Current implementation slice

The first #872 slice contains:

- canonical batch/workstream/integration read state;
- conservative dependency/overlap classification;
- bounded parallel-ready projection;
- deterministic branch-ref/idempotency policy;
- exact workstream result and Verification binding;
- explicit integration candidate/conflict state;
- stale-target blocking;
- combined validation and stale-check rejection;
- merge-readiness authorization gate;
- Control Plane-safe read projection;
- focused issue tests for the safety invariants above.

Follow-up wiring must invoke the existing #37/#82/#33/#86/#15 authorities for the actual side effects and persist the composition state durably; no new authority should be introduced to accomplish that wiring.
