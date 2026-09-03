# Frontend architecture

Issue #17 establishes the platform web client as a replaceable northbound application over the versioned Control Plane.

## Hard boundary

```text
Browser UI
    |
    v
/api/v1 Control Plane
    |
    +-- canonical Tasks / Runs / Events / Artifacts / ...
    +-- adapters and providers remain behind the platform boundary
```

The browser MUST NOT call Hermes, Forge, model gateways, MCP servers, worker transports, queues, databases, or storage backends directly. Provider-private identifiers are not navigation keys or persisted frontend identity. This preserves the same API-first invariant as CLI and future external clients.

## Implementation baseline

The initial implementation uses React + TypeScript with Vite as build/dev tooling. This is a web-client implementation choice, not a canonical platform contract. Replacing React must require no Task/Run/Event schema migration and no backend lifecycle change; the versioned Control Plane remains the compatibility boundary.

The client code is split into:

- `src/api/` — typed Control Plane models, centralized HTTP client and SSE stream client;
- `src/platform/` — canonical identifier helpers;
- `src/security/` — presentation-only permission hints; the server is authoritative;
- `src/app/` — routing, navigation and shell;
- `src/pages/` — product surfaces;
- `src/components/` — reusable states and presentation components.

## Stable navigation

The shell reserves routes for the ideal platform areas required by #17: Home, Chat, Projects/Workspaces, Tasks, Runs, Agents, Agent Teams, Verification, Organizations, Files/Artifacts, Memory, Knowledge, Search, Tools, Integrations, Models, Compute, Terminal, Automations, Approvals, Notifications, Events, Observability, Evaluations, Usage/Limits, Templates, Plugins, Import/Export and Settings.

A reserved route does not imply its owning subsystem exists. When the canonical API is absent, the page renders an explicit unavailable state. The UI never substitutes a direct provider/backend connection.

## Task / Run vertical slice

The first functional slice consumes the #32 API:

- `GET/POST /api/v1/tasks`;
- `GET /api/v1/tasks/{task_id}`;
- `POST /api/v1/tasks/{task_id}:queue|start|cancel|retry`;
- `GET /api/v1/tasks/{task_id}/runs`;
- `GET /api/v1/runs` and `/api/v1/runs/{run_id}`;
- `GET /api/v1/tasks/{task_id}/timeline`;
- `GET /api/v1/tasks/{task_id}/events/stream` via SSE.

Every mutating client call generates an `Idempotency-Key`; every HTTP request emits a correlation ID. Canonical API errors are preserved rather than flattened into provider-specific text.

## Authentication and authorization boundary

The HTTP client always uses `credentials: include` so a future #36 same-origin/session-cookie implementation can be attached without rewriting page code. An optional access-token callback exists as an integration seam; token storage is deliberately not implemented here.

Permission hooks are advisory presentation hints only. Buttons are never proof of authorization: the Control Plane remains authoritative and 401/403 responses are rendered explicitly. SSE currently relies on browser credential handling because native `EventSource` does not allow arbitrary Authorization headers.

## Live updates

Task detail subscribes to the canonical `platform.event` SSE stream and refreshes Task, Run and timeline state on canonical events. Browser-native EventSource reconnection is exposed as connection state. No WebSocket/provider event channel is introduced.

## Timeline compatibility

The timeline may contain both canonical domain events (`type=event`) and backend-neutral derived observability entries (`type=telemetry`). The frontend models both shapes and does not assume every timeline entry is a domain Event UUID.

## Accessibility and responsive baseline

The shell includes a skip link, semantic navigation/main landmarks, labelled command groups, visible focusable controls, table overflow handling and a mobile sidebar. This is a baseline rather than the final accessibility audit required for full #17 completion.

## Direct frontend dependencies

Reviewed 2026-09-03 under `LICENSE_POLICY.md` as normal package dependencies/build tooling. None changes canonical platform contracts, lifecycle ownership, persistence, distributed topology, or platform replaceability, so they are not classified as architecture-significant upstreams.

| Package | Pin | Role | License / upstream |
| --- | --- | --- | --- |
| `react`, `react-dom` | 19.2.8 | web rendering runtime | MIT, `facebook/react` |
| `vite` | 8.2.2 | dev server / production build | MIT, `vitejs/vite` |
| `@vitejs/plugin-react` | 6.1.0 | React transform / Fast Refresh | MIT, `vitejs/vite-plugin-react` |
| `typescript` | 7.0.2 | static type checking | Apache-2.0, Microsoft TypeScript/TypeScript-Go distribution |
| `vitest` | 4.1.10 | frontend unit tests | MIT; bundled license families are recorded by the package and Vite build license output |
| `@types/react`, `@types/react-dom` | 19.2.18 / 19.2.5 | development typings | MIT, DefinitelyTyped |

No upstream source is copied, vendored, forked or selectively ported into this repository. Dependency changes use explicit PR review; the Vite production build has `build.license=true` so distributed bundles retain a generated dependency-license inventory.

## Remaining #17 scope

This foundation does not close #17. Later stages still need dedicated functional pages as their owning APIs become available (Projects/Workspaces, Agents/Teams, Models, Tools, Compute, Approvals, Automations, Files/Memory/Knowledge, Observability/Evaluations, Settings, etc.), plus stronger frontend integration/acceptance tests and final UX/accessibility polish.
