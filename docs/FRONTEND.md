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

The browser MUST NOT call Hermes, Forge, model gateways, MCP servers, worker transports, queues, databases, storage backends, connector providers, PluginRegistry or PluginCatalog directly. Provider-private identifiers are not navigation keys or persisted frontend identity. This preserves the same API-first invariant as CLI and future external clients.

## Implementation baseline

The current implementation uses React + TypeScript with Vite as build/dev tooling. This is a web-client implementation choice, not a canonical platform contract. Replacing React must require no Task/Run/Event schema migration and no backend lifecycle change; the versioned Control Plane remains the compatibility boundary.

The client code is split into:

- `src/api/` — typed Control Plane models, centralized/domain collection clients, browser-session/CSRF boundary, error presentation, pagination contracts and SSE stream client;
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
- Verification — canonical verification queue/detail and review actions from #86, including Task/Run/reference evidence links and manifest-gated availability;
- Files/Artifacts — read-only canonical Artifact, Result, Plan and Step references with Task/Plan links; raw file bytes, storage paths and provider-private storage metadata are intentionally not inferred;
- Memory — canonical scoped `memory` content from #251 with scope-aware list/search/detail, explicit create, short-term promotion, supersession-based update, exact expiry and delete. Project-scoped Memory uses `workspace` plus the canonical Project ID. Origin, retention, provenance, classification and supersession links are preserved; Chat/Event/Task history is never silently reclassified as Memory;
- Knowledge — canonical `knowledge` source inventory/detail from #251 plus query-scoped `knowledge-results`. The UI supports explicit source registration, metadata update, ingestion, revisioned re-index, detach/delete tombstones and authorized retrieval with canonical source/revision citations. Retrieval rows are not treated as durable resources, and vector/index/backend-private identifiers are never exposed as canonical identity;
- Search — global canonical Search over `GET /api/v1/search` with authorization-filtered results, filters and opaque cursor pagination; unsupported semantic/hybrid modes remain optional/degraded rather than mandatory;
- Tools/Capabilities — canonical Capability inventory plus public Capability Provider descriptors, including versioned health, safety, side effects, permissions, approval requirements and schema summaries; no provider-private invocation path is invented;
- Integrations — canonical `connector-definitions` and `connections` from #44, including safe Connector metadata, Connection create/enable/disable/remove/health lifecycle and explicit `incremental|resync|rebuild` synchronization. Secret material is never accepted or rendered by this surface, and external connector actions remain behind the canonical Capability pipeline rather than a browser-side `connector.invoke` bypass;
- Models/Providers — canonical model inventory, provider inventory, health/capabilities and supported model/provider commands;
- Evaluations — canonical versioned `evaluation-suites` and durable `evaluation-runs`, including immutable configuration snapshots, evaluator/result evidence, `evaluation.run`, baseline regression comparisons through `evaluation.compare`, and Task/Run provenance links; the browser never constructs an `EvaluationRunner` or provider lifecycle state;
- Compute — canonical `nodes`, `workers` and `worker-jobs` from #14, including resource/accelerator availability, heartbeats, capabilities, placement requirements and dispatch evidence. Administrative UI is limited to the exact northbound `node.drain|undrain|maintenance-enable|maintenance-disable` and `worker.drain|undrain` commands; the browser never calls Worker transports, registries or schedulers directly;
- Terminal/Sessions — canonical Terminal session UI and Control Plane streaming gateway from #73, without exposing backend process/session handles as frontend identity;
- Automations — canonical `automations` and `automation-deliveries` management from #18, including create/update, pause/resume/disable, manual test delivery, delivery history and failed-delivery retry. Webhook ingestion, platform-event injection and scheduler evaluation remain system/integration paths rather than ordinary browser buttons;
- Plugins — canonical `plugins` and optional `plugin-candidates` from #20. Installed Plugin inventory/detail exposes state, compatibility, health, permissions, extensions, manifest and provenance. Candidate inspection pins install/update actions to the inspected manifest digest; lifecycle uses only `plugin.install|configure|enable|disable|refresh-health|validate-update|remove`. Stored configuration is intentionally not reconstructed after `plugin.configure`, and Candidate discovery degrades independently when no northbound PluginCatalog is composed;
- Approvals — read-only canonical exact-action approval queue and detail views with risk, policy, digest, Task/Run/Capability references and decision metadata; proposed payload values are not exposed and no approve/deny command is invented;
- Notifications — canonical notification inventory plus read/unread and dismiss actions from #75, gated by the advertised `notifications` resource;
- Settings/Authentication — #36 browser login, current canonical identity, browser-session inventory, renewal, targeted session revocation and logout; the HttpOnly session secret remains opaque to frontend code;
- Events/Observability — Task-scoped timeline and available backend-neutral observability information;
- Usage & Limits — canonical usage records, aggregates and budgets exposed by the Control Plane.

Paginated list surfaces use opaque server cursors. The frontend stores only cursor history required for local Previous navigation; it never decodes a cursor or derives an offset from it. Combined inventory pages such as Projects/Workspaces, Models/Providers, Agents/AgentRuns, Capabilities/Capability Providers, Connector Definitions/Connections, Evaluation suites/runs, Compute Nodes/Workers/Worker Jobs and Plugins/Plugin Candidates keep independent pagination state for each canonical collection. Memory scope/query changes reset their own cursor state, while Knowledge source inventory and query-scoped retrieval results use independent cursor histories.

Extension collections and progressive domain resources use the same versioned Control Plane and session-aware browser transport through constrained collection readers or dedicated typed domain clients. Collection names are fixed by the client rather than supplied as arbitrary browser paths, opaque cursor/filter values are forwarded without decoding, and there is no provider/private-backend fallback. Domain mutations remain explicit typed clients rather than a generic arbitrary-command surface.

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

`POST /api/v1/auth/login` and `POST /api/v1/auth/session:renew` return the separate browser CSRF token required by #36. The frontend retains only that CSRF token in same-origin `localStorage`; it is not a bearer credential or session secret. Before every unsafe cookie-authenticated request, the shared session-aware fetch boundary re-reads the current stored value and injects `X-CSRF-Token`. This keeps concurrent tabs aligned when session renewal rotates the CSRF token while allowing the HttpOnly authentication cookie to remain opaque. Existing Task, Model, Workspace, Terminal, Automation, Verification, Notification, Evaluation, Compute, Integration, Plugin, Memory and Knowledge mutations therefore inherit #36 CSRF protection without duplicating security logic in individual pages. Requests carrying an explicit Bearer `Authorization` header do not receive the browser CSRF header.

The Settings surface consumes only canonical #36 routes for login, `auth/me`, session enumeration, renewal, targeted session revocation and logout. First-user bootstrap, password recovery and credential/PAT administration are intentionally not inferred as ordinary browser workflows merely because backend hooks exist; they retain their separate operator/authorization semantics.

Authentication establishes identity only. Permission hooks remain advisory presentation hints, and buttons are never proof of authorization: the Control Plane and #15 remain authoritative. Canonical unauthenticated and authorization-denied responses are presented separately. Approval-required authorization outcomes are surfaced with their canonical approval reference when the Control Plane returns one; the frontend does not manufacture or bypass approval state.

The Approval surface is deliberately inspection-only. It consumes the canonical `approvals` ResourceService projection and shows exact-action binding metadata without proposed payload values. Decision authority remains inside #15. Until a northbound decision route exists that preserves exact digest, actor, expiry, policy and authorization semantics, the web client exposes no Approve/Deny buttons.

Automation ownership is also server-derived. Creation uses the authenticated canonical actor context rather than client-supplied identity. Configuration mutations use the exact #18 command names and remain authorization-gated server-side. Webhook configuration accepts only the canonical verification reference; embedded webhook secrets are not a browser configuration field. Delivery payloads are not rendered by default in history views.

Integration lifecycle uses the exact #44 command vocabulary. Connection creation sends only safe endpoint metadata plus canonical `SecretReference` objects; plaintext secret material is not a frontend field. Connector actions are deliberately not exposed as lifecycle commands and remain behind #12. Compute administration similarly exposes only the exact #14 Control Plane commands; browser buttons do not imply authorization and do not become scheduler authority.

Plugin lifecycle uses the exact #20 Control Plane resources and commands. Candidate installation submits the exact inspected `manifest_digest`, enable submits the installed manifest digest and update validation submits the currently discovered candidate digest. A changed manifest therefore fails closed as a conflict instead of silently installing or activating different code. Requested permissions are presentation evidence only; granted permissions remain server-resolved. Plugin configuration is write-only from this surface because the canonical Plugin resource deliberately omits stored configuration values.

Memory and Knowledge lifecycle uses only the #251 Control Plane resources and exact command vocabulary. Memory list/search remains scope-bound; supersession, promotion, expiry and deletion remain server-authoritative. Knowledge source mutation never grants the browser direct access to vector/index providers or storage backends. `knowledge-results` is consumed only for an explicit authorized retrieval query and is never linked as a standalone durable result route.

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

Reserved product routes inspect the Control Plane manifest. If a canonical resource is absent, they remain visibly unavailable and do not call private implementation services. Optional functional routes are not mounted while manifest discovery is unresolved, preventing speculative requests to unregistered collections. Multi-resource domains require every canonical collection needed by the page before the functional surface is mounted: Evaluations requires `evaluation-suites` + `evaluation-runs`, Integrations requires `connector-definitions` + `connections`, Compute requires `nodes` + `workers` + `worker-jobs`, and Knowledge requires `knowledge` + `knowledge-results` because the product surface includes source management and retrieval. Memory is independently gated by `memory`. Plugins intentionally differs: `plugins` is sufficient for installed lifecycle management, while `plugin-candidates` is optional and gates only discovery/install/update inspection. If a resource becomes advertised before its dedicated UI is implemented, the shell reports that the integration is pending rather than guessing the resource schema.

## Timeline compatibility

The timeline may contain both canonical domain events (`type=event`) and backend-neutral derived observability entries (`type=telemetry`). The frontend models both shapes and does not assume every timeline entry is a domain Event UUID.

## Accessibility and responsive baseline

The shell includes a skip link, semantic navigation/main landmarks, labelled command groups, visible focusable controls, table overflow handling, accessible pagination status/controls and a mobile sidebar. Active navigation exposes `aria-current`, menu controls reference the sidebar with `aria-controls`, API availability is announced separately from loading, and Task live-transport status is announced separately from Task lifecycle state. This is a baseline rather than a claim of a completed end-to-end accessibility audit.

## Frontend contract tests

The frontend test suite now includes focused contract coverage for:

- centralized canonical API error mapping and presentation;
- the browser-only `/api/v1` boundary for representative reads and mutations;
- idempotency and correlation headers on mutations;
- #36 browser-session login/CSRF behavior, including CSRF propagation to existing Control Plane mutations, cross-tab rotation synchronization, Bearer separation and logout cleanup;
- canonical Workspace and reference-resource contracts;
- Task-management client contracts;
- CLI/Web canonical Task-state parity fixtures;
- opaque cursor forwarding and pagination state behavior;
- independent Project/Workspace, Model/Provider, Agent/AgentRun, Capability/Capability Provider, Connector Definition/Connection, Evaluation suite/run, Compute Node/Worker/Worker Job and Plugin/Plugin Candidate cursors, plus independent Memory scope/query and Knowledge source/retrieval cursor state;
- canonical Agent, Agent Team, AgentRun, Capability and Capability Provider route forwarding;
- canonical Verification and Notification command routing through the browser-session boundary;
- canonical Connector Definition/Connection reads and exact `connection.create|enable|disable|remove|health` / `connector.sync` routing, including safe SecretReference serialization and explicit sync modes;
- canonical Evaluation suite/run collection forwarding, versioned suite references, immutable snapshot serialization, baseline invariants and `evaluation.run` / `evaluation.compare` command routing;
- canonical Compute Node/Worker/Worker Job reads, opaque cursors, canonical identifier forwarding and the exact Node/Worker administrative command vocabulary;
- canonical Plugin and Plugin Candidate reads plus exact `plugin.install|configure|enable|disable|refresh-health|validate-update|remove` routing, including manifest-digest pinning and configuration non-echo assumptions;
- canonical Memory scoped collection/detail forwarding plus exact `memory.create|promote|update|expire|delete` command routing, including opaque cursors, explicit origins/scopes, supersession semantics and preflight rejection of empty updates;
- canonical Knowledge source/detail and query-scoped `knowledge-results` forwarding plus exact `knowledge.register|update|ingest|reindex|detach|delete` routing; retrieval queries preserve source/revision citations and never create a browser-side durable result identity;
- global Search query/result navigation through the canonical Search endpoint;
- canonical Terminal session/gateway client behavior;
- constrained read-only extension collection URL/filter/cursor forwarding and path-injection rejection;
- canonical Automation create/update/lifecycle/manual-test/retry command routing with idempotency;
- manifest-gated Automation, Approval, Verification, Notification, Plugin, Memory and multi-resource Integration/Evaluation/Compute/Knowledge routing;
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

Issue #236 is the progressive integration track for the remaining reserved product domains. Before adding browser code for a domain, the northbound contract must be verified on `main`; issue status alone is not evidence that a resource or command is available. Verification (#86) and Notifications (#75) were already integrated before this track. The first #236 merge added dedicated progressive slices for Evaluations (#19), Compute (#14), Integrations (#44) and Plugins (#20). After #251 landed its canonical content lifecycle on `main`, the next #236 slice activates Memory and Knowledge over `memory`, `knowledge`, `knowledge-results` and the exact lifecycle commands described above.

An owning issue may still contain backend/distributed follow-up work while a stable northbound subset is already suitable for the browser. #14 is an example: the frontend consumes only the already-composed Node/Worker/Worker Job resources and administrative commands; remaining remote transport/reconciliation work does not justify a browser fallback into private runtime services.

At the current repository state, the remaining browser work is blocked by owning domains or missing northbound product contracts rather than by hidden frontend fallbacks:

- Organizations / Memberships still waits for #87;
- Approval decision mutation waits for a safe northbound #15 decision route; the existing `approvals` collection is intentionally read-only;
- Chat remains blocked until its owning canonical conversation/stream work is merged to `main`; a complete implementation existing only in an unmerged owning PR is not duplicated by #236;
- Templates and Import/Export remain gated until their owning issues expose the complete browser-facing canonical contract required by those product workflows.

Backend implementations existing in Python or in unmerged owning branches are not sufficient to activate a browser page. The frontend integrates a domain only after the platform exposes its versioned canonical API through the Control Plane on current `main`. Until then, the stable route remains unavailable/degraded and no browser-side fallback is permitted.
