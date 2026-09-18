# V1 Web reachability and state coverage audit (#1234)

This document records the implementation audit for #1234. It is intentionally narrower than the
final #747 product-readiness audit: #1234 closes obvious primary Web-surface gaps and establishes
guards so that #747 can perform acceptance rather than discover an unimplemented navigation area.

The authoritative frontend boundary remains the versioned Control Plane. A route being present is
not authority to mutate a resource; authorization, approval, lifecycle and persistence remain
server-owned.

## Audit method

The audit compared:

- every primary item in `frontend/src/app/navigation.ts`;
- the actual route composition in `frontend/src/app/shell/routes.tsx`;
- canonical resources/commands advertised by the current Control Plane;
- domain API clients and frontend tests;
- the user-facing V1 domains enumerated by #1234 and #747;
- the public-feature classification where it materially clarifies whether a capability is a normal
  browser product surface or an operator/integration boundary.

A UI-state requirement is **not applicable** when the canonical owner deliberately does not expose
that mutation. For example, the `files` browser surface is metadata-only; the Web client must not
invent a raw-byte mutation/download API just to satisfy a generic CRUD checklist.

## Primary V1 Web matrix

| Domain | Maintained Web surface | Canonical boundary | State/action conclusion |
| --- | --- | --- | --- |
| Authentication / sessions | sign-in boundary, `/settings` | browser-session + `auth/*` | Login, current identity, renewal, session list/revoke, logout, loading and canonical auth errors are maintained. Session revocation now requires explicit confirmation. |
| First run / onboarding | `/onboarding` | `onboarding` + canonical Project/Workspace/Agent/Model/Task commands | Maintained guided path; final real-browser acceptance remains owned by #1164. |
| Dashboard / status | `/` | health + recent canonical Task/Run state | Maintained overview with backend-owned state. |
| Projects / Workspaces | `/projects`, `/projects/:id`, `/workspaces/:id` | canonical Project/Workspace APIs | Inventory/create/detail and stable deep links exist. Workspace deep links resolve back to the Projects navigation parent. |
| Tasks / Plans / Steps / Runs / Results / Artifacts | `/tasks`, `/tasks/:id`, `/runs`, `/runs/:id`, `/plans/:id`, `/steps/:id`, `/results/:id`, `/artifacts/:id` | canonical kernel + reference collections | Task management and Run inspection are maintained; Plan/Step/Result/Artifact references have stable deep links and canonical Task/Plan relationships. |
| Agents / Agent Teams | `/agents`, `/agents/:id`, `/agent-teams`, `/agent-teams/:id` | `agents`, `agent-teams`, `agent-runs` | Inventory/detail plus canonical configuration composition are maintained; no provider identity becomes lifecycle truth. |
| Models / providers | `/models`, `/models/:id`, `/models/providers/:id` | model/provider inventory + canonical configuration commands | Inventory, health/configuration and stable detail routes exist. Routing-profile configuration is composed into the maintained Model surface. |
| Tools / Capabilities / MCP | `/tools`, `/tools/:id`, `/tools/providers/:id` | `capabilities`, `capability-providers` | Capability/provider management is the product surface. MCP servers remain replaceable provider/adapter implementations and are intentionally not contacted directly by the browser. |
| Files | `/files`, `/files/:id` | canonical `files` ResourceService | **Gap found and closed by #1234.** Authorized metadata, Project/owner scope, state, type, size/checksum and Artifact relationships are now reachable. Raw bytes, storage paths and provider-private identity remain intentionally non-Web. |
| Memory / Knowledge | `/memory`, `/memory/:id`, `/knowledge`, `/knowledge/:id` | canonical Memory/Knowledge resources and commands | Inventory/query/detail/create/update/promotion/expiry/tombstone flows are maintained. Destructive Memory/Knowledge actions already require confirmation. |
| Search | `/search` | canonical `/api/v1/search` | Global discovery is maintained over authorization-filtered canonical results; Search never becomes lifecycle authority. |
| Nodes / Workers / resources | `/compute`, Node/Worker/Job detail routes | `nodes`, `workers`, `worker-jobs` | Inventory, resource/health state and the advertised administrative command subset are maintained; scheduler/transport internals stay private. |
| Approvals / Verification | `/approvals`, `/approvals/:id`, `/verification`, `/verification/:id` | canonical Approval/Verification resources + exact commands | Inspection, decision/review actions and evidence links are maintained. Missing decision commands degrade to read-only rather than fabricating authority. |
| Automations | `/automations`, `/automations/:id` | `automations`, `automation-deliveries` + exact lifecycle commands | Create/update, pause/resume/disable, test, delivery history and retry are maintained. Scheduler evaluation and event/webhook ingestion remain system paths. |
| Notifications | `/notifications` | `notifications` | Inventory and read/dismiss attention actions are maintained; a separate durable-detail lifecycle is not invented where the owner does not require one. |
| Integrations / Connectors | `/integrations`, definition/connection detail routes | `connector-definitions`, `connections` | Connect/configure/enable/disable/health/sync/remove remain canonical. Connection removal now requires explicit confirmation. Plaintext secrets are never a browser field. |
| Repositories | `/repositories`, `/repositories/:id` | canonical repository collection/commands | **Gap found and closed by #1234.** The previous Web page exposed inventory/inspection plus fetch while the canonical V1 contract also supported registration and primary Git mutations. The maintained surface now provides managed local attach, Connection/provider discovery with optional attach, fetch, branch creation, checkout, commit, push and detach, all capability-/policy-gated through the Control Plane. |
| Marketplace / Registry | `/marketplace` | `registry-items` + Marketplace commands | Existing unified Marketplace surface is maintained and uninstall now requires explicit confirmation. #1174 is consumed; final expanded-kind closure waits for #1221 rather than guessing an unstable contract. |
| Import / Export | `/import-export`, package/preview/report deep links | `portability-packages`, `portability-import-previews`, `portability-import-reports`; `portability.export|package.validate|preview|import` | **Gap found and closed by #1234.** Export, package validation, server-owned preview and exact-preview import are reachable. Browser code cannot submit an ID mapping or mutation order. |
| Templates / generated configuration | `/templates`, `/templates/:id`; generated Workflow/Capability Assignment/Model Routing Profile detail routes | canonical Template resources/commands plus owner-domain read projections | Maintained create/version/clone/fork/preview/apply paths; generated owner-domain resources use canonical deep links when a real route exists. |
| Organizations / collaboration | `/organizations` | Organizations/Teams/Memberships/invitations/ownership/shares | #87 is closed and the maintained surface is active. Membership removal, invitation revocation and share revocation now require explicit confirmation. |
| Applications | `/applications`, `/applications/:id` | canonical Application resources | Maintained optional Application-adapter surface; availability remains manifest-gated. |
| Settings / user-manageable configuration / secret references | `/settings` plus Model/Tool/Integration/Onboarding configuration surfaces | browser auth plus canonical configuration/SecretReference contracts | User-manageable configuration is reachable in its owning domain. Resolved/plaintext secret values are intentionally not a browser-management resource. |
| Usage / Observability / diagnostics | `/usage`, `/events`, `/observability`, dashboard health | canonical accounting/timeline/observability resources | Maintained operator-readable views. Backend-neutral telemetry is not promoted into canonical Task/Run lifecycle truth. |

## Cross-cutting state coverage

The frontend centralizes state behavior rather than implementing a different security/error model per
page:

- `ErrorState` + canonical error presentation distinguish unauthenticated, access denied,
  approval-required and ordinary contract failures;
- manifest-gated routes render explicit checking/unavailable states and never call a private
  implementation when an optional canonical resource is absent;
- page-level inventories use explicit loading/empty/error states and opaque server cursors;
- mutations use typed domain clients over the shared browser-session transport, preserving CSRF,
  correlation and idempotency semantics;
- destructive actions that remove active configuration or durable user access require explicit
  confirmation where applicable;
- durable detail state is reloaded from canonical IDs instead of being kept only in transient
  frontend selection state;
- deep links now map to their stable navigation parent, including Workspace -> Projects,
  Plan/Step/Result/Artifact -> Files & Artifacts, generated Template resources -> Templates and
  Import/Export package/preview/report -> Import / Export.

#1234 also adds a navigation-closure regression guard: every discoverable primary sidebar route must
resolve to a maintained route and may not silently fall through to the generic
`Canonical subsystem unavailable` placeholder. This is the regression that would have caught the
previous Import/Export gap.

## Intentionally non-Web boundaries

The following are not treated as missing browser CRUD:

- raw File bytes, filesystem/storage-provider paths and provider-native storage identity;
- direct MCP-server, model-server, Worker-transport, scheduler, queue or database administration;
- resolved plaintext secrets; the browser uses canonical secret references only;
- backup/restore, schema upgrade and HA/failover operator procedures, which have explicit
  deployment/operator contracts and are not normal browser-user lifecycle actions;
- Automation scheduler evaluation, platform-event injection and inbound webhook delivery;
- internal compensation seams used to make failed multi-provider transactions safe.

Adding generic buttons for these boundaries would weaken, not improve, V1 architecture.

## Findings fixed by this issue

1. **Import/Export reservation without product surface** — the backend already exposed the complete
   #79 canonical workflow, but `/import-export` still fell through to a reserved unavailable page.
   Added typed Portability client, inventory/actions, stable package/preview/report routes and
   server-owned-preview enforcement.
2. **Files backend/API versus stale browser claim** — `/api/v1/files` already exposed safe,
   authorization-filtered canonical metadata while the Web UI incorrectly claimed File data was not
   northbound. Added File metadata inventory/detail and corrected that documentation.
3. **Deep-link navigation context** — multiple stable detail routes lost the active parent section
   on direct navigation. Added parent-route resolution and regression coverage.
4. **Destructive one-click actions** — Connection removal, Plugin removal, Marketplace uninstall,
   browser-session revocation and collaboration removals/revocations could execute without a
   browser confirmation. Added explicit confirmation; the Marketplace browser regression accepts
   and verifies the new confirmation.
5. **Repository workflow reachability** — the typed client already exposed repository reads/fetch/detach and the backend/CLI exposed the wider canonical management/Git contract, but normal browser users could not attach/discover repositories or reach branch/checkout/commit/push/detach workflows. Added those primary actions with capability gating, explicit confirmation for high-impact operations, approval/idempotency propagation and provider/path isolation.
6. **Documentation drift** — #87 was already closed, #79 was already browser-safe and frontend
   dependency pins had advanced beyond the documented values. `docs/FRONTEND.md` is reconciled in
   the same change.

## Remaining dependency-owned evidence

#1234 must not claim final closure from component tests alone:

- **#1164** owns the maintained real-browser first-run/E2E workflow. #1234 consumes that evidence
  instead of creating a second competing harness.
- **#1221** owns the final Marketplace expansion to Agents, Agent Teams, Orchestrators and platform
  providers. #1234 can audit the current baseline but cannot freeze the expanded route/state matrix
  before that issue stabilizes.
- **#1174** is complete and is treated as the authoritative Marketplace-core baseline.
- **#747** remains the final whole-product acceptance audit after #1234 and the other implementation
  work are complete.

The implementation branch is therefore allowed to become merge-ready before #1164/#1221 close,
but #1234 itself should remain open until those hard dependencies are consumed and the exact final
integrated Web state is rechecked.

## Validation contract

Before merging the #1234 implementation branch, the exact head must pass the repository-required
frontend and repository checks, including:

- generated frontend contract check;
- frontend transport-boundary check;
- TypeScript typecheck;
- Vitest;
- production build;
- maintained browser regression where selected by CI;
- all branch-protection Required Checks.

A green merge commit is implementation evidence, not a substitute for #1164/#1221 final dependency
consumption or #747.
