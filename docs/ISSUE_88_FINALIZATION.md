# Issue #88 finalization audit

This note records the final post-PR-161 corrections for canonical Task management.

## Terminal Task planning metadata

Task lifecycle terminality and Task-management planning metadata are separate concerns.

A `succeeded` or `cancelled` Task remains lifecycle-terminal: Task-management commands cannot reopen it or mutate its lifecycle. The Task-management service may still append canonical `task.updated` events for planning-only metadata such as `archived` and `hidden`.

Those terminal metadata writes reuse the kernel's canonical command/event commit path, so they retain the same idempotency reservation, stream revision checks, provenance, audit history and event mirroring as ordinary Task updates.

This deliberately does **not** make title/objective/lifecycle edits generally writable after terminal completion.

## Canonical WorkspaceProvider integration

When the public Control Plane is composed with the canonical #37 `WorkspaceProvider`, Task `workspace_id` validation resolves the Workspace through that provider rather than the legacy identity-only `ScopeStore`.

The resolver is asynchronous-capable so both boundaries remain supported:

- legacy/synchronous `ScopeStore` project resolution when no WorkspaceProvider is configured;
- canonical/asynchronous `WorkspaceProvider.get_workspace()` resolution when #37 is active.

A Task may reference a Workspace only when `workspace.project_id == task.project_id`. Cross-project Workspace assignment is rejected before Task-management metadata is committed.

## Archive/hidden queue visibility

The public Task queue is an active-work view by default.

If callers do not explicitly provide archive/hidden filters, the public Control Plane injects:

- `filter[archived]=false`
- `filter[hidden]=false`

Therefore archiving or hiding a Task removes it from the normal `/tasks` queue and from the default frontend queue without creating a second Task store.

Archived or hidden Tasks remain canonical resources and can still be retrieved directly by ID. API callers can query them explicitly with `filter[archived]=true` or `filter[hidden]=true`.

## Regression coverage

`tests/test_issue88_finalization.py` verifies:

- succeeded Tasks can be archived/hidden without changing `status=succeeded`;
- cancelled Tasks can be hidden without changing `status=cancelled`;
- terminal planning changes remain canonical `task.updated` events;
- the default public Task queue excludes archived and hidden Tasks;
- explicit archived/hidden filters recover those Tasks;
- a Task can reference a Workspace created through the canonical SQLite WorkspaceProvider when both share a Project;
- cross-project Workspace references through the canonical WorkspaceProvider are rejected.

These tests are in addition to the existing #88 coverage for priority, deadlines, not-before admission, responsibility/Agent assignment, dependencies, queue filters, authorization preflight, provider/orchestrator replacement and frontend integration.
