# Durable Goals (#597)

## Purpose

A Goal is a durable objective above canonical Tasks. It owns long-lived objective state, explicit success criteria, review history and Goal-to-Task provenance. A Goal never executes a Capability or Tool directly.

```text
Goal
  -> review explicit evidence
  -> decide whether work is required
  -> create/link canonical Task
  -> Task -> Plan -> Step -> Run -> Result
  -> reconcile terminal Task outcome
  -> review Goal again
```

This preserves the existing ownership boundaries:

- the Task/Run kernel remains the execution authority;
- #439 remains the Task-level planner/replanner;
- #18 remains generic schedule/event delivery rather than Goal truth;
- #15 authorizes Control Plane Goal commands and downstream Task work;
- #86 remains the authority for concrete Task/Result verification evidence;
- #19 may evaluate Goal strategies but does not mutate live Goal state.

## Canonical state and lifecycle

`GoalState` is reconstructed from an append-only `goal_*` event stream in the existing `EventRepository`. Every mutation appends a canonical Goal event containing the resulting snapshot. The existing `CommandRecord` idempotency mechanism protects Goal commands from duplicate delivery.

Goal state includes a stable ID, revision and SHA-256 digest; owner/project scope; explicit criteria; constraints and risk/data/security requirements; observation and Task-generation policy; bounded-autonomy policy; qualitative progress; evidence and review records; linked Tasks with the exact generating Goal revision; next review time; failed-cycle count; and terminal reason.

The lifecycle is independent from Task status:

```text
draft -> active <-> waiting
           ^          |
           |          v
           +------- paused

active/waiting -> satisfied | failed | cancelled | superseded
```

Invalid transitions fail deterministically. Satisfied/failed Goals require an explicit revision with `reopen_terminal=true`; cancelled/superseded Goals remain terminal.

## Criteria and evidence

The provider-neutral evaluator supports:

- `human_acceptance`;
- `metric` with `eq`, `gte`, `lte`, `gt` or `lt`;
- `canonical_state`;
- `verified_assertion` for evidence promoted through a canonical verifier boundary;
- `linked_tasks` for an explicit minimum count of reconciled successful Tasks.

Only evidence marked `verified=true` can satisfy evidence-backed criteria. Agent self-report cannot complete a Goal by itself. Human acceptance additionally requires evidence kind `human_acceptance` and value `true`.

Evidence IDs are immutable: reusing one with different content is rejected.

## Reviews and #18 Automation

Each review records an external `trigger_ref`. Scheduled/event-driven deployments should use the #18 Automation/Delivery/Event reference that caused the review. Goal state persists the Goal-specific semantics: evidence observed, criterion outcomes, work decision, reason, generated Tasks and next review time.

The Goal layer does **not** implement another scheduler. `ObservationPolicy.automation_id` is only a durable reference to generic #18 trigger delivery; Automation state never substitutes for Goal state.

## Goal -> Task bridge

`KernelGoalTaskCreator` creates executable work only through `PlatformKernel.create_task(...)`, then records exact Goal provenance through the canonical Task update path:

```json
{
  "goal_id": "goal_...",
  "goal_revision": 3,
  "goal_digest": "...",
  "goal_review_id": "goal_review_...",
  "goal_constraints": {}
}
```

The Goal itself stores a typed `GoalTaskLink`. Direct human Tasks can be linked with `goal.attach-task`.

Automatically generated Task IDs are deterministic from Goal ID, Goal revision, review idempotency key and index. A restart after Task creation but before Goal commit therefore retries the same canonical Task identity/idempotency key instead of creating duplicate work.

## Revision semantics

A revision changes objective, criteria, constraints or policy and gets a new digest. Earlier event snapshots remain immutable. Every link retains the original `goal_revision`.

`active_task_policy` is explicit:

- `retain`: existing linked Tasks remain valid;
- `supersede`: links become invalid for the new revision and active links are marked superseded in the Goal projection. This does not silently cancel the canonical Task; Task cancellation stays owned by the Task lifecycle/authorization path.

Review commands require `expected_revision`, so stale evaluators cannot mutate a newer Goal revision.

## Bounded autonomy

`AutonomyPolicy` bounds automatic work with `max_tasks_per_review`, `max_consecutive_failed_cycles` and an optional human checkpoint. The reference implementation creates at most one Task per review, never creates another Task while equivalent linked work is active, and pauses/degrades the Goal when the failure-cycle limit is reached.

Satisfied or paused/cancelled non-reviewable Goals cannot generate new work.

## Control Plane

`control_plane.goal_contract` exposes the canonical `goals` collection and commands:

```text
goal.create
goal.activate
goal.pause
goal.resume
goal.cancel
goal.revise
goal.review
goal.attach-task
goal.record-task-outcome
```

The existing Control Plane extension boundary provides northbound idempotency and #15 authorization. Goal code does not bypass it.

## Events and recovery

Committed state changes use canonical events including `goal.created`, lifecycle events, `goal.revised`, `goal.task_linked`, `goal.task_generated`, `goal.task_outcome_reconciled`, `goal.review_completed`, `goal.escalated` and `goal.satisfied`. `EventSourcedGoalRepository` can mirror committed events to the existing `EventProvider` for observability/notification projections without transferring Goal ownership.

The repository reuses `SqliteKernelRepository` for the local durable profile. Regression tests cover waiting-state restart, duplicate review delivery, restart/crash around Task creation, stale revision rejection, Task outcome reconciliation, pause/resume, revision provenance, satisfaction stopping work, and bounded failure escalation.

No paid scheduler, workflow engine, database or model service is required by the baseline implementation.
