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
| Tasks / Plans / Steps / Runs / Results / Artifacts | `/tasks`, `/tasks/:id`, `/runs`, `/runs/:id`, `/plans`, `/plans/:id`, `/steps`, `/steps/:id`, `/results`, `/results/:id`, `/artifacts`, `/artifacts/:id` | canonical kernel + reference collections | Task management and Run inspection are maintained; active Run detail exposes the canonical cancel operation with explicit confirmation and a stable return to the Run inventory. Plan/Step/Result/Artifact now have stable overview routes as well as detail deep links; refresh preserves the selected domain because collection identity is encoded in the URL. The normal chain is navigable Task -> Plan -> Step -> filtered canonical Runs -> Result/Artifact, with Task/Run relationships linking directly to canonical references. |
| Agents / Agent Teams | `/agents`, `/agents/:id`, `/agent-teams`, `/agent-teams/:id` | `agents`, `agent-teams`, `agent-runs`, canonical Task assignment projection | Inventory/create/detail/configure/clone are maintained. Detail views expose assigned canonical Tasks and their Plan/Run links without creating Agent-owned lifecycle state. Advertised `agent.delete` / `agent-team.delete` commands are surfaced only when present in the manifest and require explicit browser confirmation; server ownership/reference checks remain authoritative. |
| Models / providers | `/models`, `/models/:id`, `/models/providers/:id` | model/provider inventory + canonical configuration commands | Inventory, health/configuration and stable detail routes exist. Routing-profile configuration is composed into the maintained Model surface. |
| Tools / Capabilities / MCP | `/tools`, `/tools/:id`, `/tools/providers/:id` | `capabilities`, `capability-providers` | Capability/provider management is the product surface. MCP servers remain replaceable provider/adapter implementations and are intentionally not contacted directly by the browser. |
| Files | `/files`, `/files/:id` | canonical `files` ResourceService | **Gap found and closed by #1234.** Authorized metadata, Project/owner scope, state, type, size/checksum and Artifact relationships are now reachable. Raw bytes, storage paths and provider-private identity remain intentionally non-Web. |
| Memory / Knowledge | `/memory`, `/memory/:id`, `/knowledge`, `/knowledge/:id` | canonical Memory/Knowledge resources and commands | Inventory/query/detail/create/update/promotion/expiry/tombstone flows are maintained. Destructive Memory/Knowledge actions already require confirmation. |
| Search | `/search` | canonical `/api/v1/search` | Global discovery is maintained over authorization-filtered canonical results; Search never becomes lifecycle authority. |
| Research Evidence | `/research`, `/research/:id` | `research-items`, `research-sources`, `research-source-observations`, `research-claims`, `research-evidence` + `research.*` commands | #589 owner gap closed on the final integration follow-up: list/search/filter, stable item deep links, canonical Research creation, Source/observation/Claim/Evidence additions and revalidation are reachable when advertised; freshness, Verification bindings, workflow provenance and downstream Decision references remain inspectable without creating a Research-owned execution lifecycle. |
| Decision Records | `/decisions`, `/decisions/:id` | `decision-records` + `decision-record.*` commands | #598 owner gap closed on the final integration follow-up: list/search/filter, create, immutable supersession and confirmed withdrawal are reachable when advertised; alternatives/outcome/rationale, exact evidence/evaluation/Approval/ADR references, downstream provenance and revisit state remain inspectable. Decision history remains non-authoritative for permissions or activation. |
| Nodes / Workers / resources | `/compute`, Node/Worker/Job detail routes | `nodes`, `workers`, `worker-jobs` | Inventory, resource/health state and the advertised administrative command subset are maintained; scheduler/transport internals stay private. |
| Approvals / Verification | `/approvals`, `/approvals/:id`, `/verification`, `/verification/:id` | canonical Approval/Verification resources + exact commands | Inspection, decision/review actions and evidence links are maintained. Missing decision commands degrade to read-only rather than fabricating authority. |
| Automations | `/automations`, `/automations/:id` | `automations`, `automation-deliveries` + exact lifecycle commands | Create/update, pause/resume/disable, test, delivery history and retry are maintained. Scheduler evaluation and event/webhook ingestion remain system paths. |
| Notifications | `/notifications` | `notifications` | Inventory and read/dismiss attention actions are maintained; a separate durable-detail lifecycle is not invented where the owner does not require one. |
| Integrations / Connectors | `/integrations`, `/integrations/definitions/:id`, `/integrations/connections/:id` | `connector-definitions`, `connections` | Inventory, connect-time configuration, detail, enable/disable, health, sync, refresh and remove are maintained with stable deep links. The current canonical owner exposes no `connection.configure` / `connection.update` command, so post-connect configuration is deliberately not invented by Web; configuration is supplied to `connection.create` and later replacement is owner-defined. Connection removal requires explicit confirmation. Plaintext secrets are never a browser field. |
| Repositories | `/repositories`, `/repositories/:id` | canonical repository collection/commands | **Gap found and closed by #1234.** The previous Web page exposed inventory/inspection plus fetch while the canonical V1 contract also supported registration and primary Git mutations. The maintained surface now provides managed local attach, Connection/provider discovery with optional attach, fetch, branch creation, checkout, commit, push and detach, all capability-/policy-gated through the Control Plane. |
| Marketplace / Registry | `/marketplace`, `/marketplace/items/:resourceId` | `registry-items`, `marketplace-kinds` + Marketplace commands | #1174 and the now-integrated #1221 contracts are consumed directly. Global discovery/filtering covers Agents, Agent Teams, Orchestrators, Executors, Model Providers, Capability Providers, selected platform-provider kinds and the earlier Tool/Skill/Plugin/Connector/Application/Content kinds, while future registered kinds remain generic. Source-qualified item details now have stable reloadable deep links. Install/update/uninstall stay delegated to canonical owner handlers; configuration/enable/disable remain on the advertised owner management surface rather than becoming Marketplace-owned lifecycle. Uninstall requires explicit confirmation. |
| Import / Export | `/import-export`, package/preview/report deep links | `portability-packages`, `portability-import-previews`, `portability-import-reports`; `portability.export|package.validate|preview|import` | **Gap found and closed by #1234.** Export, package validation, server-owned preview and exact-preview import are reachable. Browser code cannot submit an ID mapping or mutation order. |
| Templates / generated configuration | `/templates`, `/templates/:id`; generated Workflow/Capability Assignment/Model Routing Profile detail routes | canonical Template resources/commands plus owner-domain read projections | Maintained create/version/clone/fork/preview/apply paths; generated owner-domain resources use canonical deep links when a real route exists. |
| Organizations / collaboration | `/organizations` | Organizations/Teams/Memberships/invitations/ownership/shares | #87 is closed and the maintained surface is active. Membership removal, invitation revocation and share revocation now require explicit confirmation. |
| Applications | `/applications`, `/applications/:id` | canonical Application resources | **Gap found and closed by #1234.** Lifecycle and diagnostics already existed, but `application.configure` was API-only and removal had no confirmation. The detail surface now edits only manifest-declared mutable typed fields through the canonical configure command and requires confirmation before removal. |
| Settings / user-manageable configuration / secret references | `/settings` plus Model/Tool/Integration/Onboarding configuration surfaces | browser auth plus canonical configuration/SecretReference contracts | User-manageable configuration is reachable in its owning domain. Resolved/plaintext secret values are intentionally not a browser-management resource. |
| Usage / Observability / diagnostics | `/usage`, `/events`, `/observability`, dashboard health | canonical accounting/timeline/observability resources | Maintained operator-readable views. Backend-neutral telemetry is not promoted into canonical Task/Run lifecycle truth. |

## Governance, automation and operations state matrix

This focused matrix records the #1234 Governance/Automation/Operations audit. A check means
the state is a deliberate Web behavior through the public Control Plane. `N/A` means the owning
V1 contract deliberately does not expose that lifecycle operation; the browser must not invent it.

| Domain | Route/navigation | List / empty / loading | Create / edit | Detail / deep link / parent | Mutations / pending | Validation | Backend / permission / offline | Refresh / stale reconciliation | Destructive confirmation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Approvals | ✓ `/approvals` | ✓ pending by default, optional terminal history | N/A — approvals are created by owning workflows | ✓ `/approvals/:id` | ✓ approve/deny only when both canonical commands are advertised; terminal records are read-only | ✓ exact approval/action/resource/policy/digest confirmation | ✓ shared typed error presentation distinguishes auth, approval, forbidden and unavailable | ✓ failed/racing decisions reload the canonical Approval while retaining stable retry identity | ✓ exact-decision confirmation is mandatory |
| Verification | ✓ `/verification` | ✓ independent opaque-cursor pagination for pending reviews, requirements and history | N/A — verification requests are lifecycle-owned | ✓ `/verification/:id` | ✓ accept/reject/request-changes with idempotent retries; terminal records remove review actions | ✓ evidence IDs and server-owned policy validation | ✓ load, requirement and action failures remain visible; missing requirement alone is not treated as failure | ✓ failed/racing review commands reload canonical Verification/requirement/history | N/A — review outcome is an explicit decision, not deletion |
| Automations | ✓ `/automations` | ✓ inventory pagination, empty/loading/error | ✓ create/update | ✓ `/automations/:id`, delivery history | ✓ pause, resume, disable, re-enable disabled state through canonical resume, revalidate invalid state, manual test and delivery retry | ✓ typed form/JSON validation plus canonical revalidation | ✓ shared typed errors; action failures reconcile canonical state | ✓ refresh inventory/detail/deliveries; failed lifecycle actions reload canonical Automation | ✓ disable confirmation; **Delete is N/A because V1 exposes no `automation.delete` command** |
| Notifications | ✓ `/notifications` | ✓ cursor-paginated inbox, filters, empty/loading, live status | N/A — notifications are projections; preferences are editable | N/A — source links lead to owning canonical resource; no invented notification lifecycle page | ✓ mark read/all read, acknowledge, dismiss, archive, delivery retry, preference mutations | ✓ canonical preference validation | ✓ shared typed errors; live-stream failure degrades to refreshable inbox | ✓ explicit refresh + live canonical reload; pagination keeps all inbox records reachable | N/A — archive/dismiss are canonical attention states, not resource deletion |
| Settings / configuration / SecretReferences | ✓ `/settings` plus owner-specific config routes | ✓ session/setup/release status loading/empty/error as applicable | ✓ only contracts explicitly declared user-manageable by their owner | ✓ owner-specific detail routes | ✓ browser session revoke, setup changes and already-maintained typed owner commands | ✓ owner manifests/schemas | ✓ shared typed errors | ✓ reload/retry in owning surfaces | ✓ session revoke; application removal and other destructive owner actions remain confirmed |
| Usage | ✓ `/usage` | ✓ independent opaque-cursor pagination for records, aggregates and budgets; available collections remain usable independently | N/A — accounting is runtime-owned | N/A — operator-readable aggregation surface | N/A — no browser accounting authority | ✓ server-owned query/filter validation | ✓ per-collection errors retain canonical forbidden/approval/unavailable/backend semantics instead of collapsing them | ✓ refresh accounting collections | N/A |
| Observability / Events | ✓ `/observability`, `/events` plus `/:taskId` deep links | ✓ recent Task chooser plus exact canonical Task ID for records outside the recent window; timeline and trace support opaque-cursor continuation with loading/empty/error states | N/A | ✓ stable Task-scoped operator permalinks and canonical Task links | N/A — telemetry is not lifecycle authority | ✓ exact Task ID and server-owned trace filters | ✓ typed read failures | ✓ explicit task/timeline refresh | N/A |
| Diagnostics | ✓ dashboard health plus `/settings` release/setup status | ✓ operator-facing health/status only | N/A | N/A | N/A | N/A | ✓ canonical health/readiness/status failures | ✓ normal page/status refresh | N/A |

Audit conclusions:

- V1 has no public `automation.delete` or `automation.enable` command. Re-enabling a disabled
  Automation is the existing canonical `automation.resume` transition; deletion is therefore
  intentionally not fabricated in the Web client.
- `automation.invalidate` / `automation.revalidate` are administrative lifecycle commands.
  The ordinary browser does not invent invalidation, but it exposes canonical revalidation when an
  Automation is already `invalid`, allowing an authorized operator to recover it.
- Secret values remain outside browser management. User-manageable configuration stores/references
  secrets only through the typed owning contract; plaintext/resolved secret material is not a V1 Web
  resource.
- Backup/restore, schema migration, HA failover, raw deployment logs, scheduler evaluation,
  platform-event injection and webhook ingress remain operational/system boundaries rather than
  newly exposed product buttons.

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
  Plan/Step/Result/Artifact overview and detail routes -> Files & Artifacts, generated Template resources -> Templates and
  Import/Export package/preview/report -> Import / Export;
- Plan/Step/Result/Artifact collection selection is URL-owned on the stable overview routes, so browser reload and direct navigation do not fall back to a transient default tab.

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
6. **Application edit/destructive-state closure** — `application.configure` was already a supported canonical command while the Web detail page rendered configuration read-only, and instance removal was a one-click destructive action. Added manifest-typed mutable configuration editing and explicit removal confirmation without exposing resolved secrets or runtime-private identities.
7. **Documentation drift** — #87 was already closed, #79 was already browser-safe and frontend
   dependency pins had advanced beyond the documented values. `docs/FRONTEND.md` is reconciled in
   the same change.

8. **Core reference refresh/deep-link gap** — Plan, Step, Result and Artifact had detail routes but their
   only overview selection lived in transient state under `/files`. Added stable `/plans`, `/steps`,
   `/results` and `/artifacts` over the same canonical collections, kept Files & Artifacts as the
   navigation parent, linked Task/Run relationships directly to those canonical detail routes, and exposed
   the canonical Step -> Run edge through Run subject filters. Agent and Agent Team details also project
   their assigned canonical Tasks with Plan/Run links from Task truth rather than keeping a second runtime view.

9. **Agent / Agent Team destructive-action gap** — the Control Plane already exposed named
   `agent.delete` and `agent-team.delete` commands, but the maintained Web detail views did not surface
   them. The typed configuration client now exposes those exact commands, the actions are manifest-gated,
   and deletion requires explicit confirmation before the server performs owner/reference safety checks.

10. **Task cancellation confirmation** — the maintained Task detail view exposed the canonical cancel
    command directly. The action now requires an explicit confirmation before invoking the existing
    Control Plane command; cancellation propagation and resulting lifecycle truth remain backend-owned.

11. **Run lifecycle reachability gap** — the typed Web client already exposed canonical Run
    cancellation, but Run detail was inspection-only and had no explicit return action. Active Run detail
    now exposes confirmed `cancelRun(task_id, run_id)`, refresh, Task navigation and a stable Back to Runs
    path; the Control Plane remains lifecycle authority.

12. **Expanded Marketplace deep-link closure** — #1221 is now closed and integrated on the M3 collection branch.
    The Marketplace already rendered its semantic kinds, owner metadata and lifecycle decisions, but
    item detail was transient selection state inside `/marketplace`. Added source-qualified
    `/marketplace/items/:resourceId` routes backed by canonical `registry-items` reads, preserving
    reload/direct-link behavior without introducing Marketplace-owned runtime authority.

## Final #1234 integration follow-up

The two owner-domain gaps identified by the earlier coverage slice are now implemented on the final
integration follow-up:

- **#589 Research Evidence** has a maintained navigation entry, canonical list/filter/create
  surface and stable Research Item detail route with Source/observation, Claim, Evidence creation
  and revalidation, freshness, Verification/provenance and downstream Decision inspection.
- **#598 Decision Records** has a maintained navigation entry, canonical list/search/filter/create
  surface and stable detail route with linked evidence, alternatives, downstream references,
  immutable supersession, confirmed withdrawal and review/revisit state.

The follow-up also fixes complete Marketplace kind-descriptor pagination and adjusts the replaceable
self-hosted authenticated-request limiter default after the official #1164 browser acceptance path
reproducibly exceeded the old implementation default. None of these changes may be treated as
accepted until the exact integrated head passes the full frontend/CI/browser gate.

## Remaining dependency-owned evidence

#1234 must not claim final closure from component tests alone:

- **#1164** owns the maintained real-browser first-run/E2E workflow. #1234 consumes that evidence
  instead of creating a second competing harness.
- **#1174** is complete and is treated as the authoritative Marketplace-core baseline.
- **#1221** is now closed and integrated through the M3 collection merge; its expanded semantic-kind
  and canonical-owner contracts are consumed by the maintained Marketplace Web surface rather than
  treated as a future dependency.
- **#747** remains the final whole-product acceptance audit after #1234 and the other implementation
  work are complete.

The implementation branch is therefore allowed to become merge-ready before #1164 closes; #1221 is already integrated.
#1234 itself should remain open until the exact final integrated Web state, including the new
#589/#598 surfaces and Marketplace/authentication follow-up, passes the full frontend and #1164
browser acceptance gate. #1221 no longer blocks this matrix.

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

A green merge commit is implementation evidence, not a substitute for #1164 final dependency
consumption or #747.
