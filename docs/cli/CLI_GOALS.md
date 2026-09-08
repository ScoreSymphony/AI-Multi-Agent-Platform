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

## Pause, resume and cancel

```bash
platform extension execute goal.pause GOAL_ID \
  --idempotency-key pause-maintain-docs

platform extension execute goal.resume GOAL_ID \
  --idempotency-key resume-maintain-docs

platform extension execute goal.cancel GOAL_ID \
  --idempotency-key cancel-maintain-docs \
  --payload '{"reason":"operator decision"}'
```

These commands remain subject to the canonical authorization/approval boundary. Pausing a Goal prevents new Goal-generated work while preserving durable Goal and Task provenance.

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

Evidence-backed criteria require canonical evidence marked as verified according to the Goal contract. Free-form Agent self-report does not become Goal truth merely because it is supplied in a review payload.

Scheduled/event reviews should normally arrive through the #18 Automation delivery integration rather than from a shell loop. #18 owns delivery timing/deduplication; the Goal subsystem owns the review semantics and durable progress state.

## Task outcome reconciliation

```bash
platform extension execute goal.record-task-outcome GOAL_ID \
  --idempotency-key reconcile-task-42 \
  --payload '{"task_id":"task_...","task_state":"succeeded"}'
```

Valid `task_state` values are `active`, `succeeded`, `failed`, `cancelled` and `superseded` according to the canonical Goal Task-link projection.

## Contract boundary

All mutation examples call `/api/v1/commands/goal.*` through the CLI's registered extension command path and require an explicit idempotency key. The CLI first verifies that the command is advertised by the current Control Plane OpenAPI document. It never imports `GoalService`, reads the EventRepository, runs a Capability directly or implements a second scheduler.
