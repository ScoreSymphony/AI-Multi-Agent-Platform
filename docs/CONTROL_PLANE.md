# Platform Control Plane and versioned API

Issue: #32

The Control Plane is the stable northbound boundary for web, CLI, automations and external clients. It exposes platform-owned canonical resources and explicit commands only. Hermes, Forge, model-provider SDKs, MCP servers, worker runtimes and other backend-private APIs are never client contracts.

## Ownership boundary

```text
Web / CLI / Automation / External Client
                  |
              /api/v1
                  |
            Control Plane
       /           |            \
 canonical      registered     provider
  kernel       domain services contracts
```

The Control Plane does not create a second Task/Run/Event domain model. Existing canonical services remain authoritative. The API serializes platform-owned state and delegates lifecycle actions back to those services.

## Issue #32 foundation scope

The foundation owned by #32 is intentionally small and contains only the resources available at that stage:

- projects;
- workspaces at identity/ownership baseline level;
- tasks;
- plans;
- steps;
- runs;
- artifacts;
- results;
- canonical task/run timeline events;
- task/run lifecycle commands;
- health/readiness;
- SSE task/run lifecycle updates;
- generated OpenAPI and common API conventions.

#32 does **not** predeclare APIs or command vocabularies for future domains such as Agents, Tools, Workers, Approvals, Automations, Evaluations, Plugins, Search or other later subsystems. Those domains extend the same Control Plane only after their canonical contracts exist.

## Current later-domain integrations

The repository can contain APIs added after #32. They are not retroactively part of the #32 foundation.

Work under #10 has already added the canonical Model Registry/Provider API, including:

- `/api/v1/models`;
- `/api/v1/model-providers`;
- their read and supported enable/disable/health commands.

These routes are legitimate because the Model domain and its Control Plane integration now exist, regardless of whether every remaining #10 deliverable is already closed. The distinction is therefore:

```text
#32 foundation contract
        +
implemented later-domain APIs
        +
explicitly registered future extensions
        =
current composed Control Plane
```

This prevents the foundation from guessing future schemas while allowing the API to grow additively.

## API versioning

The first stable major is `/api/v1`.

- Additive endpoints, optional fields and optional query parameters may be introduced within `v1`.
- Removing or renaming fields, changing their meaning/type incompatibly, changing command semantics incompatibly, or making optional input mandatory requires a new major namespace such as `/api/v2`.
- Deprecations must be documented before removal and overlap with the replacement for a migration window.
- Unsupported versions return `unsupported_api_version` with the supported versions.
- Adapter or upstream version changes do not change the northbound API major unless a canonical platform contract changes.

## Foundation commands

Lifecycle mutations use commands rather than arbitrary status patches.

Kernel-owned task/run commands include:

- `POST /api/v1/tasks/{task_id}:queue`
- `POST /api/v1/tasks/{task_id}:start`
- `POST /api/v1/tasks/{task_id}:cancel`
- `POST /api/v1/tasks/{task_id}:retry`
- `POST /api/v1/tasks/{task_id}/runs/{run_id}:cancel`

These commands delegate to canonical kernel behavior. #32 does not reserve approval, worker, plugin, automation or evaluation commands before those domains define them.

## Extension contract for later domains

Later issues extend the Control Plane through explicit platform-owned registration instead of modifying a speculative global list.

### Resource registration

A later domain can provide a `ResourceService` for its canonical collection and register it with the Control Plane:

```python
control_plane.register_resource_service("widgets", widget_service)
```

A registered collection receives the common Control Plane read conventions:

- `GET /api/v1/widgets`
- `GET /api/v1/widgets/{resource_id}`
- pagination/filter/sort/search/field selection;
- request/correlation context;
- authorization hooks;
- backend-private payload rejection;
- generated OpenAPI entries.

The collection name comes from the owning canonical domain. An unregistered future collection is neither advertised nor treated as a #32 resource.

### Command registration

A later domain can register a canonical command handler:

```python
control_plane.register_command("widget.refresh", refresh_widget)
```

Registered generic extension commands are exposed through:

```text
POST /api/v1/commands/{command}
```

The request identifies the canonical `resource_ref`. Mutating extension commands require `Idempotency-Key` and receive the same `RequestContext` used elsewhere by the Control Plane.

The owning later-domain issue may also implement dedicated canonical routes once its own contract exists, as #10 does for Models. #32 itself does not guess those routes.

### Manifest and OpenAPI

`GET /api/v1` reports the current composed surface:

- #32 foundation resources;
- APIs implemented by later-domain work;
- explicitly registered extension resources and commands;
- the OpenAPI URL;
- the live-update mechanism.

`GET /api/v1/openapi.json` generates OpenAPI 3.1 for the same current API. Future domains that have not been implemented or registered are absent.

## Query conventions

Collection endpoints standardize:

| Parameter | Meaning |
| --- | --- |
| `limit` | 1–200 items, default 50 |
| `cursor` | opaque continuation cursor |
| `sort` | stable resource field |
| `direction` | `asc` or `desc` |
| `q` | implementation-neutral search hook |
| `filter[field]` | exact canonical-field filter |
| `fields` | comma-separated sparse field selection |

Collection responses use:

```json
{
  "items": [],
  "next_cursor": null,
  "total": 0,
  "limit": 50
}
```

Stable platform IDs or stable platform references are required for resources returned through the Control Plane.

## Run error inspection

Every Run representation returned by the Control Plane contains an `error` field. This field describes the canonical execution failure state and is separate from HTTP/API request failures.

For a failed run:

```json
{
  "error": {
    "code": "run_failed",
    "category": "execution",
    "message": "executor rejected request",
    "retryable": false
  }
}
```

For a timed-out run the canonical code is `run_timed_out`, the category is `timeout`, and `retryable` is `true`. Runs in queued, starting, running, succeeded or cancelled state expose `"error": null`.

The Control Plane derives this stable error view from canonical Run status. When canonical output already contains a human-readable `error`, `message` or `reason`, that text can populate the Run error message without exposing backend exception classes or provider-private error types. The original canonical `output` remains a separate field.

This contract is available consistently through direct Run reads, task-scoped Run reads, Run lists, task start/retry results and Run cancellation results. Sparse-field Run lists may explicitly request `error` just like any other canonical Run field.

Generated OpenAPI includes `RunError`, `Run` and `RunPage` schemas and binds them to the relevant Run endpoints.

## Error model

HTTP/API request failures use a separate canonical envelope:

```json
{
  "code": "conflict",
  "message": "human-readable explanation",
  "request_id": "request_...",
  "correlation_id": "corr_...",
  "retryable": false,
  "details": {}
}
```

`ContractError` is mapped to HTTP status without exposing backend exception classes. Request and correlation IDs are returned in headers and in error bodies. Current canonical error codes have intentional HTTP mappings, including model-unavailable/no-route errors, invalid configuration, oversized input and invalid provider responses, rather than silently falling through to an accidental HTTP 500.

## Authentication and authorization context

`RequestContext` carries actor identity, owner context, request/correlation IDs and idempotency metadata. Sensitive Control Plane operations call the configured `AuthorizationProvider` using canonical action/resource references.

Authentication transport remains replaceable and is completed by its dedicated security issue. The Control Plane does not define a second policy domain.

## Live updates

`GET /api/v1/tasks/{task_id}/events/stream` provides Server-Sent Events containing canonical platform `Event` data.

When an `EventProvider` is configured, its `subscribe()` contract is used. Clients never subscribe directly to Hermes, Forge or worker-private event feeds.

`GET /api/v1/tasks/{task_id}/timeline` exposes paginated canonical event history.

## Health and readiness

- `GET /api/v1/health`
- `GET /api/v1/readiness`

Health/readiness use canonical provider contracts and remain extensible for later observability work.

## HTTP and framework boundary

`ControlPlane` is framework-independent application logic. `ControlPlaneHTTP` maps `/api/v1` to the service boundary. `ControlPlaneASGI` proves that HTTP/SSE transport works without binding the platform permanently to one web framework.

## Architecture invariants

- Clients use the Control Plane for canonical operations.
- No browser/CLI canonical flow calls Hermes, Forge, LiteLLM, MCP or Workers directly.
- Task/Run lifecycle authority remains in the canonical kernel.
- Direct database mutations do not bypass application services.
- Backend-private IDs/types remain implementation metadata.
- Future resource schemas are defined by their owning domain issues, not speculatively by #32.
- Missing future optional domains do not affect foundation startup.
