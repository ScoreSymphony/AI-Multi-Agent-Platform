# Platform Control Plane and versioned API

Issue: #32

The Control Plane is the stable northbound boundary for web, CLI, automations and external clients. It exposes platform-owned resources and explicit commands only. Hermes, Forge, model-provider SDKs, MCP servers, worker runtimes and other backend-private APIs are never client contracts.

## Ownership boundary

```text
Web / CLI / Automation / External Client
                  |
              /api/v1
                  |
            Control Plane
       /           |            \
 canonical      resource       provider
  kernel         services      contracts
```

The Control Plane does not create a second Task/Run/Event domain model. Existing canonical services remain authoritative. The API serializes their platform-owned state and delegates lifecycle actions back to those services.

## API versioning

The first stable major is `/api/v1`.

- Additive endpoints, optional fields and optional query parameters may be introduced within `v1`.
- Removing or renaming fields, changing their meaning/type incompatibly, changing command semantics incompatibly, or making optional input mandatory requires a new major namespace such as `/api/v2`.
- Deprecations must be documented before removal and overlap with the replacement for a migration window.
- Unsupported versions return `unsupported_api_version` with the supported versions.
- Adapter or upstream version changes do not change the northbound API major unless a canonical platform contract changes.

## Resource surface

`/api/v1` reserves and exposes canonical collection routes for:

- projects and workspaces;
- tasks, plans, steps and runs;
- agents and teams;
- artifacts, results and files;
- memory and knowledge;
- models and providers;
- tools and capabilities;
- nodes and workers;
- approvals;
- automations;
- evaluations;
- plugins and adapters.

The kernel-backed resources are served by the existing canonical kernel and scope services. Other domains attach through the platform-owned `ResourceService` registration seam. A missing service returns canonical `unavailable`; clients are never redirected to a concrete backend API.

Provider and capability inventory is derived only from `ProviderContract` metadata. `adapter_metadata`, backend handles, SDK objects and raw backend exceptions are not part of the northbound representation.

## Commands

Lifecycle and administrative mutations use commands rather than arbitrary status patches.

Kernel-owned task/run commands include:

- `POST /api/v1/tasks/{task_id}:queue`
- `POST /api/v1/tasks/{task_id}:start`
- `POST /api/v1/tasks/{task_id}:cancel`
- `POST /api/v1/tasks/{task_id}:retry`
- `POST /api/v1/tasks/{task_id}/runs/{run_id}:cancel`

Additional canonical commands include:

- approval approve/deny;
- adapter/plugin enable/disable;
- worker drain/restore;
- automation test run;
- evaluation start.

They are exposed through dedicated command routes and the canonical `/api/v1/commands/{command}` dispatch seam. Concrete subsystems register `CommandHandler` implementations; absent handlers return canonical `unavailable`.

Every mutating command requires `Idempotency-Key`. Task/run commands are deduplicated by the canonical kernel. Extension handlers receive the same idempotency key in `RequestContext` so their owning subsystem can apply its canonical idempotency semantics.

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

Stable platform IDs or stable platform references are required for resources returned by registered services.

## Error model

Every API failure uses one canonical envelope:

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

`ContractError` is mapped to HTTP status without exposing backend exception classes. Request and correlation IDs are returned in headers and in error bodies.

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

Provider health is normalized through `ProviderContract.health()`.

## HTTP and OpenAPI

`ControlPlane` is framework-independent application logic. `ControlPlaneHTTP` maps `/api/v1` to the service boundary. `ControlPlaneASGI` proves that HTTP/SSE transport works without binding the platform to a specific web framework.

`build_openapi()` generates the OpenAPI 3.1 specification and `/api/v1/openapi.json` serves that generated contract. Contract tests verify required resource paths, explicit command paths, error behavior, authorization propagation, pagination/filtering, live updates and backend-private type exclusion.
