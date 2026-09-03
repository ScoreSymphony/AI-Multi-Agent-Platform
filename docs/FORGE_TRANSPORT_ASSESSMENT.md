# Forge transport assessment

Status: **Current legacy public HTTP API rejected as the first concrete `ForgeClient.execute` transport.**

This assessment follows the Phase 1–3 reuse audit and the platform-owned `ForgeClient` / `ForgeExecutor` boundary added for issue #9.

## Source reviewed

Repository: `ScoreSymphony/AI-Agent-VPS`

Audited revision: `5a9f317e3bab056a4cebe214b03912a9b7ad3824`

Relevant legacy routes and implementation:

- `GET /healthz`
- `GET /api/v1/executions/{id}`
- `POST /api/v1/executions/{id}/cancel`
- `GET /api/v1/executions/{id}/logs`
- `GET /api/v1/tasks/{id}/executions`
- `POST /api/v1/tasks/{id}/launch`
- task claim/start routes and Forge task/project/workspace services

Primary inspected files:

- `core/forge/crates/api/src/lib.rs`
- `core/forge/crates/api/src/routes/executions.rs`
- `core/forge/crates/api/src/routes/tasks/execution.rs`

## Finding

The legacy API has useful **observation and cancellation** endpoints for an execution that already exists, but it does not expose a clean executor-only launch endpoint corresponding to the new platform's canonical `ExecutionRequest`.

The public manual launch path is:

`POST /api/v1/tasks/{id}/launch`

and its implementation calls Forge's own `task_service.launch_execution(id, ...)` before `task_service.start_execution(execution_id)`.

Consequently, using the existing API as `ForgeClient.execute` would require the new platform to first create or maintain a Forge Task and its surrounding Forge project/agent/workspace assumptions. That would turn backend-private execution plumbing into a shadow task lifecycle.

## Decision

**Do not implement the current task-launch HTTP API as the concrete Forge executor transport.**

This is an architecture decision, not a claim that the API is defective for its original application. It is unsuitable for this integration because issue #9 requires:

- one canonical Task/Run lifecycle owned by the new platform kernel;
- Forge-private task/execution types not becoming canonical;
- no second task/run source of truth;
- adapter-owned translation rather than architecture inheritance;
- removing/disabling Forge without migrating canonical task state.

Creating mirrored Forge Tasks merely to reach `launch_execution` would violate or weaken those requirements.

## What can still be reused from the existing HTTP API

The following route behavior is useful for a future transport once a backend execution has been created through a clean boundary:

- `/healthz` for basic process reachability;
- `GET /api/v1/executions/{id}` for backend-private execution observation;
- `POST /api/v1/executions/{id}/cancel` for cancellation;
- `GET /api/v1/executions/{id}/logs` for bounded execution evidence/log retrieval.

These routes remain backend-private. Their response types must be translated into platform `ExecutionResult`, canonical error categories and namespaced adapter metadata.

## Required shape of a future concrete transport

A concrete Forge transport should expose or wrap an executor-level operation that can start work from platform execution data **without creating a canonical Forge Task**.

The boundary needs, at minimum:

1. an external request/idempotency reference derived from the canonical Run;
2. action/instruction and filtered execution inputs;
3. an explicitly selected workspace or backend workspace reference;
4. timeout/cancellation information;
5. a returned backend execution/job reference;
6. backend status/result/error/evidence retrieval;
7. cancellation by the backend execution/job reference;
8. health/capability discovery;
9. no authority to transition the platform Task/Run directly.

Possible implementation forms include a dedicated executor-job HTTP endpoint, a small execution protocol around Forge's executor subsystem, or another adapter-local transport. The transport choice is intentionally not made until such a boundary is verified or introduced.

## Relationship to current implementation

`src/ai_multi_agent_platform/adapters/forge.py` therefore keeps `ForgeClient` as a small platform-owned protocol. The fake implementation used by contract and regression tests is deliberate: it lets the canonical translation, identity, workspace, error, cancellation and recovery semantics be completed without coupling the platform to an unsuitable legacy launch API.

This also keeps the current reuse mode truthful: adapter integration plus reference-only behavioral reuse, with no copied Forge source and no hidden requirement to run the legacy Forge application.

## Exit criterion for this transport decision

Revisit this decision when one of the following is true:

- `AI-Agent-VPS` exposes a stable executor-only start boundary that does not require Forge Task lifecycle ownership; or
- issue #9 deliberately introduces a minimal execution-only boundary around the reusable Forge executor subsystem and validates it with integration tests.

Until then, the absence of a concrete HTTP `ForgeClient` is intentional architecture protection rather than unfinished wiring by accident.
