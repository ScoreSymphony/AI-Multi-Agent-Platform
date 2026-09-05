# Task Project reassignment

Issue #157 defines Project reassignment as a canonical Task mutation. It is not a Task-management metadata field and no frontend or adapter may maintain a second Project assignment.

## Commands

- `task.project.move` moves one canonical Task.
- `task.project.bulk-move` preflights a set of independent Task moves before appending any move event.

Both commands require an `Idempotency-Key`. The single move binds the key to the requested destination and rejects reuse for a different destination.

## Canonical event

A successful move appends `task.project_reassigned` to the Task stream. The event itself keeps `project_id` equal to the **source Project** so that the event remains attributable to the scope in which the mutation occurred. Its payload records both `source_project_id` and `destination_project_id`.

The Task reducer then projects `Task.project_id = destination_project_id`. Every later kernel operation therefore receives the destination Project through the canonical Task context.

## Historical provenance

A move does not rewrite old Events, Plans, Steps, Runs, Artifacts or Results. Existing references remain attached to the Task and their original events keep their original Project attribution. In particular, a Run created before the move keeps its original `Run.project_id`; a Run created or retried after the move receives the destination Project.

This is intentional. Reassignment changes future execution scope, not history.

## Safety preflight

Before commit, the platform validates:

1. the Task is not `running` or `waiting`;
2. the Task has no non-terminal Run;
3. source and destination ownership scopes are compatible;
4. moving the Task cannot create a cross-Project parent or dependency edge, including incoming references from Tasks outside the requested move set;
5. a referenced canonical Workspace belongs to the destination Project.

The Control Plane separately authorizes the Task, the source scope and the destination scope before mutation.

## Ownership and sharing compatibility

The default policy is fail-closed:

- equal canonical owners are compatible;
- without organization data, different owners are incompatible;
- organization/team scopes within the same active organization are compatible;
- a personal user/service scope may cross into an organization/team scope when that identity has active membership in the organization;
- otherwise, two Project scopes require explicit **bidirectional** active Project sharing through the #87 ownership/share model.

Authorization remains authoritative for whether the actor may perform the move; compatibility only prevents structurally unsafe scope changes.

## Bulk semantics

The current `EventRepository` contract supports atomic append per stream, not a transaction spanning multiple Task streams. Therefore `task.project.bulk-move` reports `atomic: false`.

The command still performs authorization and relationship preflight for the entire requested set before the first append. Independent Tasks can be moved safely. A batch containing a parent/dependency edge between Tasks in the same batch is rejected with `required_capability = multi_stream_atomic_commit`: sequentially appending such a connected move could otherwise leave a cross-Project graph if a later append failed.

This fail-closed behavior is deliberate. Connected bulk moves may be enabled later if the canonical persistence boundary gains a real multi-stream atomic commit capability; callers must not infer such a guarantee today.
