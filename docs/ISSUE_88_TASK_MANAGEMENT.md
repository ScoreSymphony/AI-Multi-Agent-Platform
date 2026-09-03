# Issue #88 — Canonical Task management

This document records the platform-owned Task-management contract and frontend projection introduced for issue #88.

## Ownership

Task planning metadata is part of the canonical Task state and is persisted through the existing platform kernel as `task.updated` events under the reserved `task_management` metadata namespace.

There is deliberately no second Task table, lifecycle, scheduler or orchestrator-owned planning record.

```text
Client / UI / Automation
        |
        v
Control Plane
        |
        v
Task-management application layer
        |
        v
Platform Task/Run/Event kernel
```

Concrete orchestrators, executors, model providers and worker schedulers may consume this metadata but do not own it.

## Canonical planning fields

The contract supports:

- ordered priority: `low < normal < high < urgent`;
- due/deadline timestamp plus optional timezone metadata;
- `not_before` admission constraint;
- responsibility reference for a human/team/organization;
- canonical Agent or AgentTeam assignment reference, optional revision and policy reference;
- labels/tags;
- canonical workspace reference;
- parent/subtask reference;
- Task-to-Task `depends_on` and non-blocking `related_to` references;
- explicit manual blocking reason;
- effort hint and implementation-neutral resource hints;
- `archived` and `hidden` flags independent from lifecycle status.

Responsibility is planning metadata only. It never grants authorization.

Agent assignment references canonical Agent/AgentTeam IDs. Provider process IDs, model-provider IDs or orchestrator-private IDs are rejected as Agent assignments.

## Deadline semantics

A deadline is an intent constraint, not an execution schedule. It is distinct from Automation schedules/triggers, worker scheduling/admission, run timeouts and approval/verification expiry.

When `due_at` passes, the Task projection reports `overdue=true`. The kernel does not automatically cancel or fail the Task.

`not_before` is different: it is an admission constraint. Normal `queue`, `start` and `retry` commands are rejected until the timestamp has passed.

Deadline and not-before changes are ordinary canonical Task updates and therefore remain in Task event history.

## Task dependency policy

Dependencies reference canonical Task IDs only.

1. `depends_on` blocks normal progression until the prerequisite Task is `succeeded`.
2. A failed or cancelled prerequisite remains a blocker and is surfaced separately in `failed_dependency_ids`.
3. `related_to` is informational and does not block progression.
4. Self-dependencies are rejected.
5. Dependency cycles are rejected before state is committed.
6. Parent/dependency references must remain inside the same canonical project. This is the initial explicit cross-project policy; broader policies require a later contract change.
7. Workspace references must belong to the Task project when the Control Plane workspace registry is available.

Blocking is projected through the existing Task resource (`blocked`, `effective_blocking_reason`, `blocking_task_ids`, `failed_dependency_ids`) rather than by inventing a new lifecycle state. Existing kernel waiting information remains authoritative and is composed with Task-management blocking.

Dependency satisfaction is derived from canonical prerequisite Task lifecycle state instead of persisting a second hidden satisfaction lifecycle.

## Admission and priority

Priority affects ordering and planning only. It does not bypass authorization, approvals, capability policy, resource admission or worker eligibility.

The Control Plane gates normal queue/start/retry progression on Task-management eligibility after authorization.

Task lists use the existing canonical pagination/filter/sort conventions. `sort=priority` maps to the stable numeric priority rank and `sort=due` maps to `due_at`. Derived fields such as `overdue`, `blocked`, `responsible_id`, `agent_assignment_id`, `archived` and `hidden` are exposed on the existing Task projection and can participate in canonical filters.

## Mutations

Task-management changes are exposed as platform-owned built-in canonical commands:

- `task-management.update`
- `task-management.bulk-update`

They use the same `/api/v1/commands/...` transport as extension commands but are intentionally not stored in the issue-#32 future-extension command registry. This preserves the distinction between built-in platform behavior and independently registered extensions.

Both commands require `Idempotency-Key` and preserve the Control Plane authorization boundary.

Bulk updates preflight authorization for every targeted Task before applying any Task mutation. The current event-sourced implementation reports `atomic=false`: after authorization and validation preflight, each Task update is committed independently with a deterministic child idempotency key. A future transactional repository may strengthen this without changing the command contract.

Task creation may include the same planning fields. The Control Plane validates the planning payload first, creates the canonical Task and then records the planning metadata as an idempotent canonical `task.updated` event.

Lifecycle-sensitive operations such as queue, start, cancel and retry continue through the existing kernel-owned lifecycle routes.

## Auditability and replaceability

Every accepted planning change is persisted through `PlatformKernel.update_task()`. Priority/deadline/assignment/dependency/archive changes are therefore present in the canonical Task event history and survive process restart or adapter replacement.

Tests reconstruct the Control Plane with a replacement orchestrator over the same canonical repository and verify that Task-management metadata is unchanged. No scheduler, orchestrator or frontend owns the planning record.

## Control Plane projection

The canonical Task resource exposes the planning metadata together with derived management state, including:

- `priority` and `priority_rank`;
- `due_at`, `deadline_timezone`, `overdue` and `not_before`;
- human/team/organization responsibility;
- Agent/AgentTeam assignment reference;
- labels, workspace and parent Task;
- dependency edges and blocking prerequisite IDs;
- failed/cancelled prerequisite IDs;
- effective blocking reason and management eligibility;
- effort/resource hints;
- archive/hide state.

The Control Plane supports priority/due sorting and the existing generic Task filters for projected management fields. The Task-management commands are explicitly documented in OpenAPI without making them appear as dynamically registered extensions.

## Frontend management views

The React/TypeScript frontend consumes only the canonical Control Plane projection.

`/tasks` is the Task-management queue. It provides:

- priority, lifecycle, deadline, responsibility and blocker indicators;
- filters for status, priority, responsible assignment, blocked state and overdue state;
- priority/due/status/update ordering;
- canonical Task creation with planning metadata;
- multi-selection;
- bulk priority and archive/unarchive operations through `task-management.bulk-update`.

`/tasks/:taskId/manage` provides:

- priority, deadline/timezone and not-before editing;
- responsibility reassignment;
- Agent/AgentTeam assignment, revision, requirement and policy reference editing;
- labels, workspace, parent Task, effort and blocking metadata;
- archive/hide controls;
- dependency add/remove controls for `depends_on` and `related_to`;
- explicit prerequisite state (blocking, failed/cancelled or satisfied);
- derived eligibility/overdue/blocking state.

The existing `/tasks/:taskId` execution detail remains responsible for lifecycle operations, Runs and timeline information. The management UI therefore extends the #17 vertical slice instead of replacing or duplicating it.

Frontend-only state is limited to transient form, filter and selection state. Canonical Task planning data always comes from the Control Plane.

## Test coverage

Issue-specific tests cover:

- priority create/update/order;
- deadline and overdue calculation/filtering;
- not-before admission behavior;
- human assignment and reassignment;
- Agent/AgentTeam canonical reference validation;
- dependency satisfaction and blocker projection;
- failed/cancelled prerequisite policy;
- dependency cycle rejection;
- cross-project dependency rejection;
- bulk authorization preflight;
- canonical event-history persistence;
- orchestrator replacement without metadata loss;
- frontend command routing and idempotency;
- frontend TypeScript typechecking, unit tests and production build.

Repository CI additionally exercises Ruff format/lint, strict MyPy, the full Pytest suite, package build, LiteLLM compatibility and the real Forge sidecar integration.

## Completion boundary

Issue #88 owns practical Task-management semantics and their canonical Control Plane/UI projection. It does not own notification delivery, automation schedules, worker scheduling, verification/approval policy or a Jira/Asana-style project-management suite.

The implemented boundary now provides importance, timing, responsibility, assignment and blockers as canonical Task concerns while Task/Run execution lifecycle, authorization, orchestration and scheduling remain separate and replaceable.
