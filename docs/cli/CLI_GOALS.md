# Durable Goal CLI (#597)

The canonical Goal lifecycle is exposed through the same API-first CLI boundary as other Control Plane extension resources. The CLI does not read Goal persistence directly and does not contact an orchestrator, scheduler or Task backend.

## Inspect Goals

```bash
platform extension list goals
platform extension show goals GOAL_ID
```

The returned canonical Goal projection includes lifecycle status, qualitative progress, exact success criteria, constraints, linked/active Tasks, evidence, review history, next review time, terminal reason, revision and digest.

## Create and activate a Goal

Goal creation requires an explicit success-criteria array. The owner comes from the configured CLI profile/request context.

```bash
platform extension execute goal.create goals \
  --idempotency-key create-maintain-docs \
  --payload '{
    "title": "Maintain documentation quality",
    "objective": "Keep documentation aligned with the canonical source",
    "success_criteria": [
      {
        "criterion_id": "accepted",
        "kind": "human_acceptance",
        "description": "A human accepts the maintained documentation state",
        "operator": "truthy",
        "target": true,
        "required": true
      }
    ]
  }'

platform extension execute goal.activate GOAL_ID \
  --idempotency-key activate-maintain-docs
```

## Pause, resume, cancel and fail

```bash
platform extension execute goal.pause GOAL_ID \
  --idempotency-key pause-maintain-docs

platform extension execute goal.resume GOAL_ID \
  --idempotency-key resume-maintain-docs

platform extension execute goal.cancel GOAL_ID \
  --idempotency-key cancel-maintain-docs \
  --payload '{"reason":"operator decision"}'

platform extension execute goal.fail GOAL_ID \
  --idempotency-key fail-maintain-docs \
  --payload '{"reason":"required source can no longer be recovered"}'
```

These commands remain subject to the canonical authorization/approval boundary. Pausing a Goal prevents new Goal-generated work while preserving durable Goal and Task provenance. `goal.fail` is a reasoned terminal decision for an active/waiting Goal; the reason is preserved as `terminal_reason` and emitted through `goal.failed`. A failed Goal cannot resume directly and requires an explicit versioned revision with `reopen_terminal=true` to pursue a changed objective again.

## Revise a Goal

A revision must name the expected current Goal revision. This prevents a stale operator/evaluator from overwriting a newer objective.

```bash
platform extension execute goal.revise GOAL_ID \
  --idempotency-key revise-maintain-docs-r4 \
  --payload '{
    "expected_revision": 3,
    "objective": "Maintain documentation and examples against the current source",
    "active_task_policy": "retain"
  }'
```

Use `"active_task_policy":"supersede"` when linked work should no longer be treated as valid for the new Goal revision. This changes Goal linkage semantics; it does not silently cancel the canonical Task.

To reopen a `satisfied` or `failed` Goal, the revision must be explicit:

```bash
platform extension execute goal.revise GOAL_ID \
  --idempotency-key reopen-maintain-docs-r5 \
  --payload '{
    "expected_revision": 4,
    "objective": "Pursue the revised recoverable documentation target",
    "reopen_terminal": true,
    "active_task_policy": "supersede"
  }'
```

## Attach an existing Task

```bash
platform extension execute goal.attach-task GOAL_ID \
  --idempotency-key attach-task-42 \
  --payload '{"expected_revision":4,"task_id":"task_..."}'
```

Task creation itself remains owned by the canonical Task surface. Goal-generated Tasks and directly attached Tasks retain Goal/revision provenance rather than becoming private planner state.

## Trigger an explicit Goal review

```bash
platform extension execute goal.review GOAL_ID \
  --idempotency-key manual-review-5 \
  --payload '{
    "expected_revision": 4,
    "trigger_ref": "manual:cli",
    "evidence": []
  }'
```

Direct CLI/Web review cannot self-promote non-human evidence: a client-supplied `verified=true` claim is rejected and evidence actor identity is bound to the authenticated principal. Authenticated user `human_acceptance` is promoted at the Control Plane boundary; all other verified evidence must arrive through a canonical verification/promotion integration such as #86.

Scheduled/event reviews should normally arrive through the #18 Automation delivery integration rather than from a shell loop. #18 owns delivery timing/deduplication; the Goal subsystem owns the review semantics and durable progress state.

A successful review commits canonical review observability atomically with Goal state. Depending on the decision, the Goal stream can include `goal.review_started`, `goal.progress_criterion_changed`, `goal.work_not_needed`, `goal.blocked`, `goal.escalated`, `goal.task_generated` or `goal.satisfied`, followed by the state-bearing `goal.review_completed` event. Replaying the same idempotency key does not duplicate these events.

## Task outcome reconciliation

Task terminal state is not writable through a northbound Goal command. The canonical Task/Run kernel emits terminal Task events, and the Goal runtime projects `task.succeeded`, `task.failed` and `task.cancelled` into matching Goal Task links before #18 advances the event cursor. This prevents CLI/Web clients from claiming Task completion independently of the canonical Task lifecycle.

## Contract boundary

All mutation examples call `/api/v1/commands/goal.*` through the CLI's registered extension command path and require an explicit idempotency key. The CLI first verifies that the command is advertised by the current Control Plane OpenAPI document. It never imports `GoalService`, reads the EventRepository, runs a Capability directly or implements a second scheduler.
