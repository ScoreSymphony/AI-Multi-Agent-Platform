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

The current implementation uses React + TypeScript with Vite as build/dev tooling. This is a web-client implementation choice, not a canonical platform contract. Replacing React must require no Task/Run/Event schema migration and no backend lifecycle change; the versioned Control Plane remains the compatibility boundary.

The client code is split into:

- `src/api/` — typed Control Plane models, centralized HTTP client, browser-session/CSRF boundary, error presentation, pagination contracts and SSE stream client;
- `src/platform/` — canonical identifier helpers;
- `src/security/` — presentation-only permission hints; the server is authoritative;
- `src/app/` — routing, navigation, shell and reusable cursor-pagination state;
- `src/pages/` — product surfaces;
- `src/components/` — reusable states, pagination and presentation components.

## Stable navigation

The shell reserves routes for the ideal platform areas required by #17: Home, Chat, Projects/Workspaces, Tasks, Runs, Agents, Agent Teams, Verification, Organizations, Files/Artifacts, Memory, Knowledge, Search, Tools, Integrations, Models, Compute, Terminal, Automations, Approvals, Notifications, Events, Observability, Evaluations, Usage/Limits, Templates, Plugins, Import/Export and Settings.

A reserved route does not imply its owning subsystem exists or is composed northbound. When the canonical API is absent, the page renders an explicit unavailable state. The UI never substitutes a direct provider/backend connection.

## Current functional surfaces

The initial shell has progressed beyond the first Task/Run vertical slice. The currently integrated surfaces use canonical Control Plane resources only:

- Home/Overview — canonical health plus recent Task and Run activity;
- Projects/Workspaces — Project list/create/detail and Workspace list/create/detail, including canonical Workspace lifecycle fields where a Workspace provider is composed and explicit `identity_only` fallback otherwise;
- Tasks — canonical Task management, including the practical #88 queue/filter/sort/metadata/dependency/bulk-management surface already exposed by the Control Plane;
- Runs — canonical Run list/detail;
- Agents — canonical Agent definitions with immutable current revision, role, model/capability/data-access policy metadata and read-only AgentRun evidence linked through canonical Agent/Task/Run IDs;
- Agent Teams — canonical Team definitions with immutable current revision, exact pinned member Agent revisions, delegation relationships, shared capabilities and runtime limits;
- Files/Artifacts — read-only canonical Artifact, Result, Plan and Step references with Task/Plan links; raw file bytes, storage paths and provider-private storage metadata are intentionally not inferred;
- Search — global canonical Search over `GET /api/v1/search` with authorization-filtered results, filters and opaque cursor pagination; unsupported semantic/hybrid modes remain optional/degraded rather than mandatory;
- Tools/Capabilities — canonical Capability inventory plus public Capability Provider descriptors, including versioned health, safety, side effects, permissions, approval requirements and schema summaries; no provider-private invocation path is invented;
- Models/Providers — canonical model inventory, provider inventory, health/capabilities and supported model/provider commands;
- Terminal/Sessions — canonical Terminal session UI and Control Plane streaming gateway from #73, without exposing backend process/session handles as frontend identity;
- Settings/Authentication — #36 browser login, current canonical identity, browser-session inventory, renewal, targeted session revocation and logout; the HttpOnly session secret remains opaque to frontend code;
- Events/Observability — Task-scoped timeline and available backend-neutral observability information;
- Usage & Limits — canonical usage records, aggregates and budgets exposed by the Control Plane.

Paginated list surfaces use opaque server cursors. The frontend stores only cursor history required for local Previous navigation; it never decodes a cursor or derives an offset from it. Combined inventory pages such as Projects/Workspaces, Models/Providers, Agents/AgentRuns and Capabilities/Capability Providers keep independent pagination state for each canonical collection.

## Task / Run vertical slice

The Task/Run workflow consumes the versioned Control Plane:

- `GET/POST /api/v1/tasks`;
- `GET /api/v1/tasks/{task_id}`;
- `POST /api/v1/tasks/{task_id}:queue|start|cancel|retry`;
- `GET /api/v1/tasks/{task_id}/runs`;
- `GET /api/v1/runs` and `/api/v1/runs/{run_id}`;
- `GET /api/v1/tasks/{task_id}/timeline`;
- `GET /api/v1/tasks/{task_id}/events/stream` via SSE.

Every mutating client call generates an `Idempotency-Key`; every HTTP request emits a correlation ID. Canonical API errors are preserved rather than flattened into provider-specific text.

## Authentication and authorization boundary

The shell composes the #36 browser-session boundary in front of the shared `ControlPlaneClient`. All requests continue to use `credentials: include`; the opaque session secret exists only in the server-issued `HttpOnly` cookie and is never read or persisted by frontend code.

`POST /api/v1/auth/login` and `POST /api/v1/auth/session:renew` return the separate browser CSRF token required by #36. The frontend retains only that CSRF token in tab-scoped `sessionStorage`. For unsafe cookie-authenticated requests, the shared session-aware fetch boundary injects `X-CSRF-Token` before the request reaches the Control Plane. Existing Task, Model, Workspace and Terminal mutations therefore inherit #36 CSRF protection without duplicating security logic in individual pages. Requests carrying an explicit Bearer `Authorization` header do not receive the browser CSRF header.

The Settings surface consumes only canonical #36 routes for login, `auth/me`, session enumeration, renewal, targeted session revocation and logout. First-user bootstrap, password recovery and credential/PAT administration are intentionally not inferred as ordinary browser workflows merely because backend hooks exist; they retain their separate operator/authorization semantics.

Authentication establishes identity only. Permission hooks remain advisory presentation hints, and buttons are never proof of authorization: the Control Plane and #15 remain authoritative. Canonical unauthenticated and authorization-denied responses are presented separately. Approval-required authorization outcomes are surfaced with their canonical approval reference when the Control Plane returns one; the frontend does not manufacture or bypass approval state.

SSE relies on browser credential handling because native `EventSource` does not allow arbitrary Authorization headers. #36 authenticates the stream request server-side before the canonical event transport constructs its request context.

## Live updates

Task detail subscribes to the canonical `platform.event` SSE stream and refreshes Task, Run and timeline state on canonical events.

Browser-native EventSource reconnect behavior is exposed as transport connection state (`connecting`, `open`, `reconnecting`, `closed`). That transport state is not Task lifecycle state and is not recovery authority.

Canonical `platform.error` payloads are handled separately from transport reconnects. The UI surfaces their category/code/message and request reference as a degraded state while the latest successfully read Task data remains usable. A later successful stream open or canonical event clears the stale live-stream error. Malformed canonical event/error payloads are surfaced explicitly rather than silently converted into Task state.

## Error, unavailable and degraded states

Common loading, empty, error and degraded components are used across integrated pages. Canonical error presentation distinguishes at least:

- unauthenticated/session-required outcomes;
- authorization denial;
- approval-required authorization outcomes;
- unavailable/retryable subsystem failures;
- ordinary contract/request failures.

The shell also distinguishes initial Control Plane discovery (`Checking API`) from a real manifest failure (`API unavailable`), so accessibility live regions do not announce a false outage during normal startup.

Reserved product routes inspect the Control Plane manifest. If a canonical resource is absent, they remain visibly unavailable and do not call private implementation services. Optional functional routes are not mounted while manifest discovery is unresolved, preventing speculative requests to unregistered collections. If a resource becomes advertised before its dedicated UI is implemented, the shell reports that the integration is pending rather than guessing the resource schema.

## Timeline compatibility

The timeline may contain both canonical domain events (`type=event`) and backend-neutral derived observability entries (`type=telemetry`). The frontend models both shapes and does not assume every timeline entry is a domain Event UUID.

## Accessibility and responsive baseline

The shell includes a skip link, semantic navigation/main landmarks, labelled command groups, visible focusable controls, table overflow handling, accessible pagination status/controls and a mobile sidebar. Active navigation exposes `aria-current`, menu controls reference the sidebar with `aria-controls`, API availability is announced separately from loading, and Task live-transport status is announced separately from Task lifecycle state. This is a baseline rather than a claim of a completed end-to-end accessibility audit.

## Frontend contract tests

The frontend test suite now includes focused contract coverage for:

- centralized canonical API error mapping and presentation;
- the browser-only `/api/v1` boundary for representative reads and mutations;
- idempotency and correlation headers on mutations;
- #36 browser-session login/CSRF behavior, including CSRF propagation to existing Control Plane mutations, Bearer separation and logout cleanup;
- canonical Workspace and reference-resource contracts;
- Task-management client contracts;
- CLI/Web canonical Task-state parity fixtures;
- opaque cursor forwarding and pagination state behavior;
- independent Project/Workspace, Model/Provider, Agent/AgentRun and Capability/Capability Provider cursors;
- canonical Agent, Agent Team, AgentRun, Capability and Capability Provider route forwarding;
- global Search query/result navigation through the canonical Search endpoint;
- canonical Terminal session/gateway client behavior;
- canonical SSE URL, credential handling, event/error delivery and reconnect/close state;
- explicit unavailable/degraded navigation behavior and manifest-gated optional routes;
- shell accessibility status semantics, including loading versus actual API outage and the real Settings/session route.

These tests validate the client boundary; backend lifecycle, authorization and persistence remain owned by their canonical services.

## Frontend toolchain reproducibility

The frontend declares `packageManager: npm@11.6.0` and CI installs that exact npm release before resolving dependencies. This is deliberate: npm 10.9.8 has an Arborist `edgesOut` crash on the supported Node 22 dependency-resolution path. Node 22.22.2+ is used because npm 11's engine range requires it. The package-manager pin is tooling only and does not alter platform runtime contracts.

## Direct frontend dependencies

Reviewed 2026-09-03 under `LICENSE_POLICY.md` as normal package dependencies/build tooling. None changes canonical platform contracts, lifecycle ownership, persistence, distributed topology, or platform replaceability, so they are not classified as architecture-significant upstreams.

| Package | Pin | Role | License / upstream |
| --- | --- | --- | --- |
| `react`, `react-dom` | 19.2.8 | web rendering runtime | MIT, `facebook/react` |
| `vite` | 8.2.2 | dev server / production build | MIT, `vitejs/vite` |
| `@vitejs/plugin-react` | 6.1.0 | React transform / Fast Refresh | MIT, `vitejs/vite-plugin-react` |
| `typescript` | 7.0.2 | static type checking | Apache-2.0, Microsoft TypeScript |
| `vitest` | 4.1.10 | frontend unit tests | MIT; bundled license families are recorded by the package and Vite build license output |
| `@types/react`, `@types/react-dom` | 19.2.18 / 19.2.5 | development typings | MIT, DefinitelyTyped |

No upstream source is copied, vendored, forked or selectively ported into this repository. Dependency changes use explicit PR review; the Vite production build has `build.license=true` so distributed bundles retain a generated dependency-license inventory.

## Progressive API-gated scope

The #17 shell is established. Reserved routes are activated progressively by their owning domain issues when a canonical northbound Control Plane resource/command is actually composed.

At the current repository state, the remaining browser work is split between APIs that are still absent and newly available APIs whose dedicated #17 presentation has not yet been integrated:

- Nodes / Workers / Compute still waits for #14;
- Verification / Review still waits for #86;
- Organizations / Memberships still waits for #87;
- Notifications still lacks its dedicated canonical browser surface;
- Memory/Knowledge content management remains beyond the currently available reference/provider-level data;
- canonical read-only `approvals` now exists and can be integrated as an inspection queue, but approve/deny must not be invented before the exact #15 decision route exists;
- canonical `automations` and `automation-deliveries` now exist and can be integrated through their #18 Control Plane contracts;
- Plugins waits for the #20 Control Plane/CLI lifecycle work to merge and stabilize;
- other later product surfaces such as Evaluations, Templates and Import/Export remain progressive according to their owning canonical contracts.

Backend implementations existing in Python are not sufficient to activate a browser page. The frontend integrates a domain only after the platform exposes its versioned canonical API through the Control Plane. Until then, the stable route remains unavailable/degraded and no browser-side fallback is permitted.
