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

`GoalState` is reconstructed from an append-only `goal_*` event stream in the existing `EventRepository`. Every mutation appends canonical Goal events, with the command's final event carrying the resulting snapshot. The existing `CommandRecord` idempotency mechanism protects Goal commands from duplicate delivery.

Goal state includes a stable ID, revision and SHA-256 digest; owner/project scope; explicit criteria; constraints and risk/data/security requirements; observation and Task-generation policy; bounded-autonomy policy; qualitative progress; evidence and review records; linked Tasks with the exact generating Goal revision; next review time; failed-cycle count; and terminal reason.

The lifecycle is independent from Task status:

```text
draft -> active <-> waiting
           ^          |
           |          v
           +------- paused

active/waiting -> satisfied | failed | cancelled | superseded
```

Invalid transitions fail deterministically. `goal.fail` provides an explicit reasoned terminal failure transition from active/waiting pursuit. Satisfied/failed Goals require an explicit revision with `reopen_terminal=true`; cancelled/superseded Goals remain terminal.

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

Each review records an external `trigger_ref`. Scheduled/event-driven deployments use the canonical #18 Automation/Delivery that caused the review. Goal state persists the Goal-specific semantics: evidence observed, criterion outcomes, work decision, reason, generated Tasks and next review time.

The Goal layer does **not** implement another scheduler. `ObservationPolicy.automation_id` is a durable reference to the #18 Automation responsible for admitting, deduplicating and retrying review deliveries. Automation state never substitutes for Goal state.

The production-shaped Control Plane composes Goal review dispatch into the existing Automation task-creation seam. An Automation opts into Goal review by carrying an exact Goal binding in its canonical `TaskTemplate.payload`:

```json
{
  "goal_observation": {
    "goal_id": "goal_...",
    "goal_revision": 3
  }
}
```

The revision is intentionally static. A delivery created for revision 3 cannot silently mutate revision 4 after the objective, success criteria, constraints or policy have changed. The Goal must be revised together with its observation Automation binding.

Ordinary Automations remain ordinary Task-producing Automations. Only an explicitly marked `goal_observation` delivery is intercepted by the Goal dispatcher. #18 therefore remains the single scheduling/event-delivery authority and no second generic scheduler is introduced.

A Goal review can legitimately complete without creating a Task. In that case the #18 delivery succeeds with `generated_task_id = null`; the platform does not fabricate no-op work merely to satisfy the old TaskCreator return shape.

### Evidence trust boundary

Delivery payload is a trigger, not proof. Webhook/event payload fields are never promoted directly into verified `GoalEvidence`, even if an untrusted payload claims `verified=true`. A deployment that wants a delivery to contribute evidence must provide a `GoalEvidenceResolver` backed by a canonical verification/promotion boundary such as #86. Without that resolver the delivery still causes a review, but contributes no new authoritative evidence.

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

The Task metadata above is also the planner-facing #439 provenance boundary: planners receive ordinary canonical Tasks whose metadata preserves the exact Goal revision/digest/review and constraint snapshot that caused the work. Goal state does not become a planner-private input channel.

## Revision semantics

A revision changes objective, criteria, constraints or policy and gets a new digest. Earlier event snapshots remain immutable. Every link retains the original `goal_revision`.

`active_task_policy` is explicit:

- `retain`: existing linked Tasks remain valid;
- `supersede`: links become invalid for the new revision and active links are marked superseded in the Goal projection. This does not silently cancel the canonical Task; Task cancellation stays owned by the Task lifecycle/authorization path.

Review commands require `expected_revision`, so stale evaluators cannot mutate a newer Goal revision. The #18 bridge applies the same guard using the revision embedded in the Automation template.

## Bounded autonomy

`AutonomyPolicy` bounds automatic work with `max_tasks_per_review`, `max_consecutive_failed_cycles` and an optional human checkpoint. The reference implementation creates at most one Task per review, never creates another Task while equivalent linked work is active, and pauses/degrades the Goal when the failure-cycle limit is reached.

Satisfied, failed, paused or cancelled non-reviewable Goals cannot generate new work. A satisfied/failed Goal can only return to active pursuit through an explicit versioned revision with `reopen_terminal=true`.

## Control Plane and single-node composition

`control_plane.goal_contract` exposes the canonical `goals` collection and commands:

```text
goal.create
goal.activate
goal.pause
goal.resume
goal.cancel
goal.fail
goal.revise
goal.review
goal.attach-task
goal.record-task-outcome
```

The production-shaped single-node Control Plane composes this collection and command set automatically. `GoalService` uses an `EventSourcedGoalRepository` over the same durable `EventRepository` as the Task/Run kernel, and generated work uses the same `PlatformKernel` instance. Goal persistence and Task persistence therefore share the same local restart boundary without introducing a Goal-specific database or execution stack.

The existing Control Plane extension boundary provides northbound idempotency and #15 authorization. Goal code does not bypass it.

## CLI and Web projection

The canonical Goal resource projection contains the complete Goal state together with stable `id`, resource `type`, current version, active Task IDs and stream revision. This is the only state source for CLI/Web clients; frontend or CLI code must not maintain a second Goal lifecycle model.

CLI inspection uses the registered extension collection:

```text
platform extension list goals
platform extension show goals <goal_id>
```

The current API-first CLI can also invoke **registered** canonical extension commands through `platform extension execute`. It first discovers the command from `x-registered-extension-commands`, requires an explicit resource reference and uses an idempotency key for mutation. Goal lifecycle operations therefore use the same canonical `/api/v1/commands/goal.*` handlers as Web and other Control Plane clients; there is no direct Goal repository/service bypass. `docs/cli/CLI_GOALS.md` documents the Goal-specific payloads and safe invocation examples, including explicit terminal failure with a reason.

The Web surface uses a typed Goal client for `/api/v1/goals` and `/api/v1/commands/goal.*`. `/goals` exposes inventory and draft creation; `/goals/:goalId` exposes lifecycle/progress, criteria, constraints, linked Tasks, evidence/review history, revision provenance and the supported lifecycle/review/revision/attach operations. Canonical Task creation remains owned by the Task surface and can then be linked to the exact Goal revision.

## Events and recovery

Goal review observability is committed atomically with the review state change. Supplementary events carry review/domain details without a second snapshot; the final `goal.review_completed` event carries the resulting canonical snapshot and command idempotency record. A replay of the same review command therefore does not duplicate review telemetry.

Canonical Goal events include:

- lifecycle: `goal.created`, `goal.activated`, `goal.paused`, `goal.resumed`, `goal.cancelled`, `goal.failed`, `goal.revised`;
- review cycle: `goal.review_started`, `goal.review_completed`;
- progress/evidence decision: `goal.progress_criterion_changed`, `goal.work_not_needed`;
- work/provenance: `goal.task_generated`, `goal.task_linked`, `goal.task_outcome_reconciled`;
- attention/outcomes: `goal.blocked`, `goal.escalated`, `goal.satisfied`.

`EventSourcedGoalRepository` can mirror every committed event to the existing `EventProvider` for observability/notification projections without transferring Goal ownership. This is the #75-compatible notification hook; a richer notification UX does not need to become Goal authority.

The repository reuses `SqliteKernelRepository` for the local durable profile. Regression tests cover waiting-state restart, duplicate review delivery, restart/crash around Task creation, stale revision rejection, Task outcome reconciliation, pause/resume, revision provenance, satisfaction stopping work, bounded failure escalation, explicit terminal failure/reopen, and durable review-event sequencing/idempotency.

The runtime integration suite additionally proves that the composed Control Plane registers the Goal surface, #18 delivery replay cannot duplicate Goal work, a monitoring-only review succeeds without fabricating a Task, untrusted delivery payload is not treated as verified Goal evidence, and stale Automation revision bindings fail closed.

No paid scheduler, workflow engine, database or model service is required by the baseline implementation.
