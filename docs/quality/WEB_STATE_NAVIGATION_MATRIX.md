# Cross-cutting Web state and navigation matrix (#1234)

This audit is the cross-domain companion to `WEB_V1_COVERAGE_1234.md`. It records the Web-state
semantics that should remain consistent across the V1 product without introducing a browser-owned
lifecycle or a second Browser E2E harness.

The baseline audited here is current `main` after the M3 integration batch. It already contains the
#1259 Web-coverage slice, the maintained #1164 browser first-run path and the #1221 Marketplace
kind expansion. This branch therefore consumes those implementations instead of recreating them.

## Legend

- **shared** — covered by common shell, `States.tsx`, canonical error presentation, typed Control
  Plane clients or the common manifest gate.
- **domain** — the owning page has an explicit domain-specific state or action.
- **manifest** — optional surface is fail-closed behind advertised Control Plane resources/commands.
- **ID reload** — durable detail state is recovered from the canonical ID after reload, not from
  frontend selection state.
- **URL** — relevant query/filter state is represented in the URL and survives reload/back-forward.
- **N/A** — the canonical owner does not expose this state/action as a normal Web lifecycle.
- **OWNER GAP** — the canonical backend owner claims a V1 Web surface but no maintained route exists;
  implementation belongs to that owning-domain chat rather than this cross-cutting branch.

## Matrix

| Domain | Route | Loading | Empty | Validation | Backend Error | Permission | Offline | Refresh | Deep Link | Back |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Authentication / sessions | sign-in boundary, `/settings` | shared | domain session list | domain forms | shared | domain auth boundary | shared Control Plane availability | canonical browser session | `/settings` | browser history |
| First run / onboarding | `/onboarding` | shared/domain | N/A | domain model/project/workspace forms | shared | canonical session/policy | domain provider-unavailable state; #1164 evidence | **#1164 proven** | maintained route | advisory/resumable onboarding + browser history; no global navigation gate |
| Dashboard / status | `/` | shared | domain recent Task/Run tables | N/A | domain partial degradation + shared failures | canonical filtered reads | shared | canonical reload | `/` | browser history |
| Chat | `/chat` | shared/domain | domain | domain composer/actions | shared | canonical | manifest / provider errors | canonical reload | maintained route | browser history |
| Projects | `/projects`, `/projects/:id` | shared/domain | domain | domain create/edit | shared | canonical | shared | ID reload | yes | prefix parent `/projects` |
| Workspaces | `/workspaces/:id` | shared/domain | domain where applicable | domain | shared | canonical | shared | ID reload | yes | **#1259 parent → Projects** |
| Repositories | `/repositories`, `/repositories/:id` | shared/domain | domain | domain attach/discovery/Git forms | shared | canonical policy/approval | provider/connection failures + manifest | ID reload | yes | prefix parent |
| Tasks | `/tasks`, `/tasks/:id`, `/tasks/:id/manage` | shared/domain | domain | domain creation/commands | shared | canonical + hint only | shared/provider errors | ID reload | **#1164/#1234 browser evidence** | prefix parent |
| Plans | `/plans/:id` | shared | N/A detail | N/A read projection | shared | canonical | shared | ID reload | yes | **#1259 parent → Files** |
| Steps | `/steps/:id` | shared | N/A detail | N/A read projection | shared | canonical | shared | ID reload | yes | **#1259 parent → Files** |
| Runs | `/runs`, `/runs/:id` | shared/domain | domain | N/A read projection | shared | canonical | shared/executor state | ID reload | yes | prefix parent |
| Results | `/results/:id` | shared | N/A detail | N/A read projection | shared | canonical | shared | ID reload | **#1164 proven** | **#1259 parent → Files** |
| Artifacts | `/artifacts/:id` | shared | N/A detail | N/A read projection | shared | canonical | shared | ID reload | **#1164 link evidence** | **#1259 parent → Files** |
| Files | `/files`, `/files/:id` | shared/domain | domain | N/A metadata-only boundary | shared | canonical | manifest/storage errors through CP | ID reload | yes | prefix parent |
| Goals | `/goals`, `/goals/:id` | shared/domain | domain | domain | shared | canonical | manifest/shared | ID reload | yes | prefix parent |
| Templates | `/templates`, `/templates/:id` | shared/domain | domain | domain preview/apply/forms | shared | canonical | strict multi-resource manifest gate | ID reload | yes | prefix parent |
| Generated template configuration | `/workflows/:id`, `/capability-assignments/:id`, `/model-routing-profiles/:id` | shared | N/A detail | N/A read projection | shared | canonical | manifest | ID reload | yes | **#1259 parent → Templates** |
| Agents | `/agents`, `/agents/:id` | shared/domain | domain | domain configuration | shared | canonical | manifest/model/provider errors | ID reload | yes | prefix parent |
| Agent Teams | `/agent-teams`, `/agent-teams/:id` | shared/domain | domain | domain composition | shared | canonical | manifest/provider errors | ID reload | yes | prefix parent |
| Models / providers | `/models`, `/models/:id`, `/models/providers/:id` | shared/domain | domain | domain configuration | shared | canonical | domain provider health/unavailable | ID reload | yes | prefix parent |
| Tools / Capabilities / MCP providers | `/tools`, `/tools/:id`, `/tools/providers/:id` | shared/domain | domain | domain assignments/config | shared | canonical | manifest/provider unavailable | ID reload | yes | prefix parent |
| Memory | `/memory`, `/memory/:id` | shared/domain | domain | domain create/update | shared | canonical | manifest/provider errors | ID reload | yes | prefix parent |
| Knowledge | `/knowledge`, `/knowledge/:id` | shared/domain | domain | domain register/update/query | shared | canonical | strict multi-resource manifest gate/provider errors | ID reload | yes | prefix parent |
| Search | `/search?...filters` | shared/domain | domain | **URL input validation** | shared | authorization-filtered results | optional mode degradation + shared | **URL** | **URL** | **URL/back-forward** |
| Evaluations | `/evaluations`, suite/run detail routes | shared/domain | domain | domain run/compare inputs | shared | canonical | multi-resource manifest/provider errors | ID reload | yes | prefix parent |
| Learning | `/learning`, `/learning/:id` | shared/domain | domain | domain promotion inputs | shared | canonical | strict manifest/capability gate | ID reload | yes | prefix parent |
| Nodes / Workers / Worker Jobs | `/compute`, node/worker/job detail routes | shared/domain | domain | command-specific | shared | canonical | multi-resource manifest + worker offline state | ID reload | yes | prefix parent |
| Applications | `/applications`, `/applications/:id` | shared/domain | domain | domain manifest-typed configure | shared | canonical | multi-resource manifest/runtime errors | ID reload | yes | prefix parent |
| Terminal | `/terminal` | shared/domain | domain session state | domain session inputs | shared | canonical | manifest/gateway unavailable | canonical server state | maintained route | browser history |
| Automations | `/automations`, `/automations/:id` | shared/domain | domain | domain create/update | shared | canonical | manifest/target provider errors | ID reload | yes | prefix parent |
| Plugins | `/plugins`, installed/candidate detail routes | shared/domain | domain | domain install/config/update | shared | canonical | manifest/candidate provider degradation | ID reload | yes | prefix parent |
| Approvals | `/approvals`, `/approvals/:id` | shared/domain | domain | domain decision comment/action binding | shared | canonical; read-only when decision commands absent | manifest/shared | ID reload | yes | prefix parent |
| Verification | `/verification`, `/verification/:id` | shared/domain | domain | domain review action | shared | canonical | manifest/provider errors | ID reload | yes | prefix parent |
| Integrations / Connectors | `/integrations`, definition/connection details | shared/domain | domain | domain connection/config forms | shared | canonical | multi-resource manifest + connector health | ID reload | yes | prefix parent |
| Marketplace / Registry | `/marketplace` | shared/domain | domain | domain filters/install/update preview | shared | canonical | manifest/source unavailable | canonical reload | maintained route | browser history |
| Notifications | `/notifications` | shared/domain | domain | N/A for durable detail | shared | canonical | manifest/shared | canonical reload | maintained route | browser history |
| Organizations / collaboration | `/organizations` | shared/domain | domain | domain membership/invite/share forms | shared | canonical | manifest/shared | canonical reload | maintained route | browser history |
| Governance | `/governance`, proposal/specification detail routes | shared/domain | domain | domain owner forms where exposed | shared | canonical | multi-resource manifest/shared | ID reload | yes | prefix parent |
| Import / Export | `/import-export`, package/preview/report details | shared/domain | domain | server-owned preview validation | shared | canonical | strict multi-resource manifest | ID reload | yes | **#1259 parent** |
| Events | `/events` | shared/domain | domain | N/A read projection | shared | canonical | shared | canonical reload | maintained route | browser history |
| Observability / diagnostics | `/observability` | shared/domain | domain | N/A read projection | shared | canonical | partial/degraded data | canonical reload | maintained route | browser history |
| Usage / limits | `/usage` | shared/domain | domain | N/A read projection | shared | canonical | manifest/shared | canonical reload | maintained route | browser history |
| User-manageable config / secret references | `/settings` + owning Model/Tool/Integration/Application surfaces | shared | domain-specific | domain; secret references only | shared | canonical | shared/provider-specific | canonical reload | owning route | owning parent/history |
| Research Evidence (#589) | `/research`, `/research/:id` | shared/domain | domain | URL filter validation + canonical query validation | shared | canonical owner-scoped resources | shared/Control Plane | ID reload + explicit refresh | yes | prefix parent |
| Decision Records (#598) | `/decisions`, `/decisions/:id` | shared/domain | domain | URL filter validation + canonical query validation | shared | canonical visibility/authorization | shared/Control Plane | ID reload + explicit refresh | yes | prefix parent |

## Pending / mutation-state audit

The requested table keeps the exact columns from the #1234 audit prompt, so mutation-pending state is
recorded separately. The maintained mutation-heavy surfaces inspected in this pass (including
Onboarding, Tasks, Repositories, Models, Compute, Integrations, Plugins, Templates, Memory,
Knowledge, Automations, Approvals, Verification, Applications and Organizations) use explicit
busy/pending state and release it from `finally` paths after both success and failure. Controls are
disabled or relabelled while the mutation is in flight where the owning page exposes an action.

No common "stuck busy" pattern was found that justified a second lifecycle abstraction. This branch
therefore keeps pending state page-local while fixing the actual shared stuck-state gap: failed
manifest discovery can now recover in-place.

## Cross-cutting findings closed in this branch

### 1. Unknown routes were conflated with unavailable optional subsystems

The route fallback previously rendered `UnavailablePage` for an unknown URL. That made a typo or stale
bookmark look like an optional provider/resource outage. Unknown URLs now render a dedicated
`NotFoundPage`, while manifest-gated resources keep their existing unavailable/degraded semantics.

Malformed percent-encoded detail URLs are also rejected by `matchPath` without throwing during
route rendering.

### 2. Error presentation did not distinguish the required state classes precisely enough

Canonical browser errors now distinguish:

- authentication required;
- approval required;
- permission denied;
- not found / deleted-or-no-longer-visible resources;
- validation failures;
- stale/conflicting canonical state;
- Control Plane transport outage;
- optional subsystem unavailable;
- generic contract/backend failure.

The common `ErrorState` exposes **Retry** only when the canonical Control Plane error envelope marks
the failure `retryable`. A 403, 404, validation error or arbitrary local exception therefore no
longer presents a misleading generic retry action.

### 3. Manifest discovery could remain stuck after a transient Control Plane failure

The authenticated shell now retains the canonical manifest error and exposes a retry through the
shared error state. A successful retry re-runs public manifest discovery and restores manifest-gated
routes without a full browser restart. While the manifest itself is unavailable, resource gates defer
to that shell-level Control Plane outage instead of simultaneously rendering a misleading optional
"Canonical subsystem unavailable" state. Once a manifest is available, genuinely absent optional
resources keep their existing unavailable/degraded presentation. The browser still does not infer
missing resources or mount private fallbacks.

### 4. Router state discarded query strings

The lightweight router previously tracked only `pathname`; internal navigation with query state was
not a supported first-class location. It now tracks `pathname` and `search`, updates both on
`popstate`, preserves same-origin query/hash navigation, leaves external links to the browser, and
keeps route matching independent from the query string.

### 5. Global Search filters were refresh-local component state

Search filters now serialize into the canonical browser URL. Supported values are parsed
fail-closed, invalid mode/sort/direction/limit values fall back to supported defaults, and the form
is restored from the URL after reload and Back/Forward. Cursor pagination remains transient and
server-opaque; it is intentionally reset when the filter query changes. The prior result page is
cleared while the new query is pending so a changed URL/filter cannot silently present stale results.

## Browser evidence

#1234 does not introduce another browser harness. The existing
`frontend/tests/browserFirstRun.browser.mjs` maintained by #1164 is extended only with representative
cross-domain checks after the official first run has completed:

- direct Task detail navigation and reload;
- Search query/filter reload persistence;
- explicit unknown-route Not Found behavior;
- browser Back restoring the Search URL-owned filter state;
- direct-detail permission denial with no misleading Retry action;
- a once-valid detail resource becoming Not Found;
- a bounded manifest/Control Plane transport outage followed by in-place shell recovery.

The existing #1164 provider-offline/recovery and onboarding reload checks remain the evidence for
provider-specific failure and incomplete-setup persistence.

## Existing #1259 behavior deliberately reused

This branch does **not** duplicate:

- the primary-route closure guard;
- deep-link parent mapping for Workspace, Plan, Step, Result, Artifact and generated Template
  resources;
- the destructive confirmations already added for Connection, Plugin, Marketplace, session,
  collaboration, Application and Repository actions.

## Final owner-domain integration

The earlier #589/#598 owner gaps are implemented on the final #1234 integration follow-up:

1. **#589 Research Evidence** now has discoverable list/filter and stable detail routes over the
   canonical Research collections, including Sources/observations, Claims, Evidence freshness,
   Verification bindings, originating workflow references and downstream Decision references
   derived from canonical Decision Records.
2. **#598 Decision Records** now has discoverable list/search/filter and stable detail routes with
   alternatives/outcome, exact evidence/evaluation/Approval/ADR references, downstream provenance,
   supersession/withdrawal history and review/revisit state.

These remain subject to exact-head frontend and browser validation before #1234 acceptance.

## Acceptance boundary

This matrix is evidence for #1234, not a reason to close #1234 from this branch. Final issue closure
must consume the dedicated owner-domain slices, validate the exact integrated head and then allow
#747 to remain the final whole-product acceptance audit.
