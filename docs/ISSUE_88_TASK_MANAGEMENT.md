# Issue #88 — Canonical Task management

This document records the platform-owned Task-management contract and frontend projection introduced for issue #88, including the post-merge hardening audit.

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

Agent assignment validates the canonical reference contract: `agent_<uuid>` for an Agent and `team_<uuid>` for an AgentTeam, plus optional revision/required/policy metadata. Provider process IDs, model-provider IDs and orchestrator-private IDs are rejected. Durable existence/revision resolution against platform-owned Agent/AgentTeam resources composes with #33 when that registry is implemented; #88 does not invent a substitute Agent registry.

Likewise, responsibility references remain permission-neutral and compose with the durable organization/team/membership resources from #87.

## Deadline semantics

A deadline is an intent constraint, not an execution schedule. It is distinct from Automation schedules/triggers, worker scheduling/admission, run timeouts and approval/verification expiry.

When `due_at` passes, the Task projection reports `overdue=true`. The kernel does not automatically cancel or fail the Task.

`not_before` is different: it is an admission constraint. Normal `queue`, `start` and `retry` commands are rejected until the timestamp has passed.

Deadline and not-before changes are ordinary canonical Task updates and therefore remain in Task event history.

### Upcoming/range queries

The Control Plane supports query-only absolute range filters:

- `filter[due_after]=<ISO-8601 aware timestamp>` — inclusive lower bound;
- `filter[due_before]=<ISO-8601 aware timestamp>` — inclusive upper bound.

These filters are evaluated against canonical `due_at` and removed before the generic equality-filter/pagination layer. They do not persist an `upcoming` flag or any second clock-dependent queue state. Invalid, timezone-naive or reversed ranges are rejected.

The frontend maps user-facing presets such as “due in 24h”, “due in 7 days” and “due in 30 days” into those absolute bounds at query time.

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

Task lists use the existing canonical pagination/filter/sort conventions. `sort=priority` maps to the stable numeric priority rank and `sort=due` maps to `due_at`.

The queue can filter canonical projected fields including:

- lifecycle `status`;
- `priority`;
- `project_id`;
- `responsible_type` / `responsible_id` for user/team/organization responsibility queues;
- `agent_assignment_type` / `agent_assignment_id` for Agent/AgentTeam queues;
- derived `assignment_state=assigned|unassigned` across both responsibility and Agent assignment;
- `blocked`;
- `overdue`;
- `archived` / `hidden` where requested by API clients;
- absolute `due_after` / `due_before` ranges.

These are derived/query views, not separate canonical Task stores.

## Mutations

Task-management changes are exposed as platform-owned built-in canonical commands:

- `task-management.update`
- `task-management.bulk-update`

They use the same `/api/v1/commands/...` transport as extension commands but are intentionally not stored in the issue-#32 future-extension command registry. This preserves the distinction between built-in platform behavior and independently registered extensions.

Both commands require `Idempotency-Key` and preserve the Control Plane authorization boundary.

Bulk updates preflight authorization for every targeted Task before applying any Task mutation. The current event-sourced implementation reports `atomic=false`: after authorization and validation preflight, each Task update is committed independently with a deterministic child idempotency key. A future transactional repository may strengthen this without changing the command contract.

Task creation may include the same planning fields. The Control Plane validates the planning payload first, creates the canonical Task and then records the planning metadata as an idempotent canonical `task.updated` event.

Lifecycle-sensitive operations such as queue, start, cancel and retry continue through the existing kernel-owned lifecycle routes.

### Project reassignment boundary

The original #88 product-operation examples also mentioned moving Tasks between Project scopes. The hardening audit determined that this must **not** be implemented as `task_management.project_id` metadata.

Canonical `Task.project_id` is a core scope field established by the Task kernel. A real move must define source/destination authorization, historical Event/Plan/Run provenance, Workspace and Task-dependency consistency, ownership/sharing semantics and the scope used by future execution.

That work is explicitly extracted to **#157 — Add canonical Task project reassignment and move semantics**. #88 exposes and filters the canonical `project_id` but never creates a shadow project assignment.

## Auditability and replaceability

Every accepted planning change is persisted through `PlatformKernel.update_task()`. Priority/deadline/assignment/dependency/archive changes are therefore present in the canonical Task event history and survive process restart or adapter replacement.

Tests reconstruct the Control Plane over the same canonical repository with explicit replacement Orchestrator and Lifecycle-provider implementations and verify that Task-management metadata is unchanged. No scheduler, orchestrator, lifecycle provider or frontend owns the planning record.

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
- archive/hide state;
- the canonical Task `project_id` owned by the core Task domain.

The Task-management OpenAPI extension documents native commands, deadline range filters and the supported queue-filter dimensions without making built-in commands appear as dynamically registered extensions.

## Frontend management views

The React/TypeScript frontend consumes only the canonical Control Plane projection.

`/tasks` is the Task-management queue. It provides:

- priority, lifecycle, deadline, responsibility and blocker indicators;
- filters for status and priority;
- Project queue filtering;
- assigned/unassigned filtering;
- responsibility type/ID filtering, including Team queues;
- Agent/AgentTeam type/ID filtering;
- blocked-state filtering;
- overdue plus upcoming 24h/7d/30d views;
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

- priority create, successful update and deterministic ordering;
- deadline and overdue calculation/filtering;
- absolute upcoming/deadline range queries and invalid range rejection;
- not-before admission behavior;
- human assignment and reassignment;
- Agent canonical-ID reference validation;
- AgentTeam canonical-ID reference validation;
- Project, Team, Agent/AgentTeam and assigned/unassigned queue filters;
- dependency satisfaction and blocker projection;
- failed prerequisite policy;
- cancelled prerequisite policy;
- dependency cycle rejection;
- cross-project dependency rejection;
- bulk authorization preflight;
- canonical event-history persistence;
- explicit Orchestrator and Lifecycle-provider replacement without metadata loss;
- frontend command routing and idempotency;
- frontend TypeScript typechecking, unit tests and production build.

Repository CI additionally exercises Ruff format/lint, strict MyPy, the full Pytest suite, package build, LiteLLM compatibility and the real Forge sidecar integration.

## Completion boundary

Issue #88 owns practical Task-management semantics and their canonical Control Plane/UI projection. It does not own notification delivery, automation schedules, worker scheduling, verification/approval policy, canonical Task Project reassignment (#157) or a Jira/Asana-style project-management suite.

The implemented boundary provides importance, timing, responsibility, assignment, Project/Team/Agent queue visibility and blockers as canonical Task-management concerns while Task/Run execution lifecycle, authorization, orchestration, canonical Project scope and scheduling remain separate and replaceable.
