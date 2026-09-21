# Platform Control Plane and versioned API

The Control Plane is the stable northbound transport and foundation boundary for web, CLI, automations and external clients. It exposes platform-owned canonical resources and explicit commands only. The stability of each domain/resource contract composed behind that boundary is classified separately. Hermes, Forge, model-provider SDKs, MCP servers, worker runtimes and other backend-private APIs are never client contracts.

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

## Foundation scope

The stable Control Plane foundation is intentionally small and contains only its canonical foundation resources:

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

The Control Plane foundation does **not** predeclare APIs or command vocabularies for independent domains such as Agents, Tools, Workers, Approvals, Automations, Evaluations, Plugins or Search. Those domains extend the same Control Plane only through their canonical contracts.

## Current registered-domain integrations

Registered domain APIs beyond this foundation are not retroactively part of the foundation contract.

The Model domain adds the canonical Model Registry/Provider API, including:

- `/api/v1/models`;
- `/api/v1/model-providers`;
- their read and supported enable/disable/health commands.

These routes are legitimate because the Model domain and its Control Plane integration exist. The distinction is therefore:

```text
Control Plane foundation contract
        +
implemented registered-domain APIs
        +
explicitly registered future extensions
        =
current composed Control Plane
```

This prevents the foundation from guessing future schemas while allowing the API to grow additively.

## API versioning and stability

The first stable major for the shared Control Plane foundation/protocol is `/api/v1`.

The repository-wide role/stability vocabulary is defined in [`../FEATURE_CLASSIFICATION.md`](../FEATURE_CLASSIFICATION.md). The `control-plane-v1` classification is **Core + Stable**, but that classification covers the shared `v1` protocol/foundation conventions and stable foundation behavior rather than automatically promoting every registered domain resource to Stable. Each registered-domain public contract keeps the stability level declared for its own feature entry.

A later domain can therefore expose a Beta or Experimental resource through the same composed `/api/v1` Control Plane without downgrading the Stable foundation and without acquiring Stable compatibility by namespace inheritance. API-major stability and feature maturity are related but non-overlapping concepts. ADR 0003 still governs the northbound wire contract: a breaking canonical Control Plane contract change requires a new major namespace regardless of the feature's maturity label.

- Additive endpoints, optional fields and optional query parameters may be introduced within `v1` when they preserve existing canonical contracts.
- Removing or renaming a `v1` field/command/resource, changing its meaning or type incompatibly, or making previously optional canonical input mandatory is a breaking northbound contract change and requires a new Control Plane major such as `/api/v2`, regardless of whether the affected feature is Stable, Beta or Experimental.
- Beta and Experimental resources composed under `/api/v1` retain their own maturity labels and do not inherit Stable compatibility merely from the namespace. Their bounded evolution must still use the Control Plane's versioned replacement mechanism for incompatible wire-contract changes; maturity governs the support/deprecation promise around that versioned transition rather than permitting in-place reinterpretation of `v1`.
- Control Plane deprecations overlap with their versioned replacement for a documented migration window as required by ADR 0003. Feature maturity may impose additional compatibility guidance but does not relax that northbound versioning rule.
- Unsupported Control Plane majors return `unsupported_api_version` with the supported versions.
- Adapter or upstream version changes do not change the northbound API major unless they require a breaking canonical Control Plane contract change.
- Generated frontend DTOs inherit the role/stability metadata of the canonical resource they represent; generation does not promote Beta/Experimental resources to Stable.

## Foundation commands

Lifecycle mutations use commands rather than arbitrary status patches.

Kernel-owned task/run commands include:

- `POST /api/v1/tasks/{task_id}:queue`
- `POST /api/v1/tasks/{task_id}:start`
- `POST /api/v1/tasks/{task_id}:cancel`
- `POST /api/v1/tasks/{task_id}:retry`
- `POST /api/v1/tasks/{task_id}/runs/{run_id}:cancel`

These commands delegate to canonical kernel behavior. The foundation does not reserve approval, worker, plugin, automation or evaluation commands before those domains define them.

## Extension contract for additional domains

Additional domains extend the Control Plane through explicit platform-owned registration instead of modifying a speculative global list.

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
- recursive backend-private payload rejection;
- generated OpenAPI entries, including the common `filter[field]` convention.

The collection name comes from the owning canonical domain. An unregistered collection is neither advertised nor treated as a foundation resource.

A new public registered resource or command must also declare its canonical owner, architectural role and stability under [`../FEATURE_CLASSIFICATION.md`](../FEATURE_CLASSIFICATION.md). Registration proves composition into the Control Plane; it does not by itself create a Stable compatibility promise.

### Command registration

A later domain can register a canonical command handler:

```python
control_plane.register_command("widget.refresh", refresh_widget)
```

Registered generic extension commands are exposed through:

```text
POST /api/v1/commands/{command}
```

The request must use `Content-Type: application/json`, requires an `Idempotency-Key`, and contains the canonical target as `resource_ref`. Additional JSON members form the domain-owned command payload:

```json
{
  "resource_ref": "widget_...",
  "reason": "maintenance"
}
```

The request identifies the canonical `resource_ref`. Mutating extension commands receive the same `RequestContext` used elsewhere by the Control Plane. Generated OpenAPI documents the JSON request body and required `resource_ref` field.

The owning domain may also implement dedicated canonical routes once its own contract exists, as the Model domain does. The Control Plane foundation does not guess those routes.

### Manifest and OpenAPI

`GET /api/v1` reports the current composed surface:

- foundation resources;
- APIs implemented by later-domain work;
- explicitly registered extension resources and commands;
- the OpenAPI URL;
- the live-update mechanism.

`GET /api/v1/openapi.json` generates OpenAPI 3.1 for the same current API. Future domains that have not been implemented or registered are absent. The specification documents the error statuses that the Control Plane can intentionally emit, including authentication, payload-size, media-type and semantic-validation failures.

Role/stability metadata may be emitted by generated documentation/DTO tooling where useful, but it remains descriptive metadata. It must never be interpreted as authorization, health, enablement or conformance state.

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
  "category": "conflict",
  "message": "human-readable explanation",
  "request_id": "request_...",
  "correlation_id": "corr_...",
  "retryable": false,
  "details": {}
}
```

Every API error has both a stable specific `code` and a stable broader `category`. Categories group errors without erasing the actionable code; examples include `validation`, `configuration`, `resource`, `availability`, `provider`, `conflict`, `timeout`, `capacity`, `authentication`, `authorization`, `contract` and `backend`.

`ContractError` is mapped to HTTP status without exposing backend exception classes. Request and correlation IDs are returned in headers and in error bodies. Current canonical error codes have intentional HTTP mappings, including model-unavailable/no-route errors, invalid configuration, oversized input and invalid provider responses, rather than silently falling through to an accidental HTTP 500.

## Authentication and authorization context

`RequestContext` carries actor identity, owner context, request/correlation IDs and idempotency metadata. Sensitive Control Plane operations call the configured `AuthorizationProvider` using canonical action/resource references.

Authentication transport remains replaceable and is completed by its dedicated security issue. The Control Plane does not define a second policy domain.

## Live updates

`GET /api/v1/tasks/{task_id}/events/stream` provides Server-Sent Events containing canonical platform `Event` data.

Each `platform.event` frame carries the canonical Event ID as the SSE `id:` field. Browser
reconnects may resume with the standard `Last-Event-ID` header; explicit clients may use the
equivalent `after_event_id` query parameter. The Control Plane forwards either cursor to the
canonical Event provider/repository so reconnects do not replay already-consumed Task history.

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
- Backend-private IDs/types remain implementation metadata and are rejected even when nested inside registered extension payloads.
- Additional resource schemas are defined by their owning domains, not speculatively by the Control Plane foundation.
- Missing future optional domains do not affect foundation startup.