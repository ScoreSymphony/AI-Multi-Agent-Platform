# Cross-client Web / CLI / API conformance

Issue #1236 treats the public Web, CLI and HTTP API surfaces as projections of one canonical Control Plane. The server owns lifecycle, authorization, validation, idempotency and pagination semantics; clients may adapt presentation only.

The operation-level final **Core** audit is retained in
[`CROSS_CLIENT_CORE_AUDIT_1236.md`](CROSS_CLIENT_CORE_AUDIT_1236.md). Marketplace/provider-kind
and real-browser/first-run acceptance remain separately owned tracks and are not treated as Core
evidence merely because this aggregate document also records them.

## Invariants

- Canonical IDs and resource states originate from the versioned `/api/v1` Control Plane.
- Web and CLI must not invent lifecycle transitions or call provider-private mutation paths.
- Equivalent list/search operations preserve the same filter, sort, cursor and field-selection semantics.
- Canonical error envelopes preserve HTTP status, code, category, retryability, request/correlation identity and safe details across clients.
- Client-side retries are bounded transport behavior only; they must not create a second logical mutation.
- Deep links use canonical public resource IDs. Provider-private IDs are metadata only.
- Unsupported/deprecated capabilities are rendered as such rather than silently substituted.

## Representative V1 behavior matrix

| Workflow | Canonical API authority | CLI projection | Web projection | Cross-client evidence / current gate |
| --- | --- | --- | --- | --- |
| Authentication/session bootstrap | auth/session resources and canonical HTTP auth errors | profile/auth commands call Control Plane | browser auth/session boundary uses ApiTransport | Maintained browser first-run covers first-admin bootstrap, reload, logout/login and completed-session resume through the public boundary |
| Projects / Workspaces | `/projects`, `/workspaces` | list/show/create via canonical client | ControlPlaneClient project/workspace methods | Browser first-run creates both through `/api/v1`; the returned canonical IDs are immediately re-read through public GET resources and the Workspace keeps the same Project identity |
| Task lifecycle | `/tasks`, queue/start/cancel/retry actions | task commands use only `/api/v1` | ControlPlaneClient task methods | Shared parity tests plus real browser first-run Task creation; a separate public-API-created Task is cancelled outside the page and the Web observes the same canonical revision/status after reload |
| Plan / Step progress | plan-coordination / workflow projections | task workflow commands | workflow progress client/UI | The maintained #1164 browser first-run emits the real Plan/Steps and #1236 re-resolves every emitted Plan/Step ID through public `/api/v1` resources |
| Run status / cancellation | `/runs`, task-run cancellation action | run commands | ControlPlaneClient run methods | Shared Run fixture plus browser first-run checks that every Step Run ID/status agrees with `/api/v1/runs/{id}`; lifecycle mutation remains server-owned |
| Result / Artifact inspection | canonical result/artifact resources | result/reference inspection | canonical reference/detail clients | Browser first-run Result/Artifact IDs resolve through public API and rendered deep links use those exact canonical IDs |
| Agents / Agent Teams | agent/team resources and commands | canonical resource/extension commands | typed agent/team clients | Browser first-run researcher/developer/reviewer Agent IDs resolve through `/api/v1/agents/{id}`; the existing standard-catalog bootstrap step also proves installed/preserved Agent Team identities through `/api/v1/agent-teams`; #1221 Marketplace semantics remain separate |
| Models / providers | model/provider resources | model/model-provider commands | typed model clients/pages | Same Control Plane resources; provider-private IDs must not leak |
| Tools / Capabilities | capability resources/invocation policy | canonical capability/reference paths | typed capability client/pages | Existing capability conformance; no client-owned invocation authority |
| Approvals | approval resources/actions | canonical approval commands | ApprovalClient | Existing approval policy tests; permissions remain server-owned |
| Verification | verification resources/actions | canonical resource commands | VerificationClient | Existing verification gate; completion cannot be client-bypassed |
| Search | `/search` query contract | typed search command | SearchPage / ControlPlaneClient.search | Shared query contract; authorization filtering remains server-owned |
| Automations | automation resources/actions | canonical extension/domain commands | AutomationClient | Existing automation acceptance evidence; lifecycle remains canonical |
| Workers / Nodes | worker/node resources/actions | compute commands | compute clients/pages | Supported-state projection only; scheduler/worker authority is server-side |
| Marketplace | `registry-items`, `marketplace-kinds`, `marketplace.preview|install|update|uninstall` | `platform marketplace ...` over the same resources/commands | `RegistryClient` + `/marketplace` over the same resources/commands | #1174 core + #1221 semantic kinds are integrated; shared Marketplace fixtures and owner-bound tests now gate cross-client parity |
| Diagnostics / status | manifest, health, readiness | platform status/health/doctor | setup/status surfaces | Canonical API status is source of truth |

## Marketplace / Registry / provider closure

The final Marketplace pass consumes the accepted #1174 core and #1221 semantic-kind extension as
integrated by the M3 collection merge (#1271). It does **not** define a second Marketplace model.

### Authoritative V1 kinds

`BUILTIN_MARKETPLACE_KINDS` is the authority for built-in kinds. At this acceptance point it
registers:

`agent`, `agent_team`, `orchestrator`, `executor`, `model_provider`,
`capability_provider`, `memory_provider`, `file_provider`, `knowledge_provider`,
`observability_exporter`, `automation_provider`, `evaluator`, `tool`, `skill`,
`plugin`, `workflow`, `template`, `model_configuration`, `connector`,
`application`, `evaluation` and `documentation`.

The kind registry remains extensible: an additional server-registered string kind is renderable by
Web/CLI without adding a client enum. Clients may group or humanize a kind for presentation, but
they may not synthesize lifecycle support.

### Cross-client Marketplace matrix

| Concern | Canonical Control Plane / API | CLI projection | Web projection | Acceptance invariant |
| --- | --- | --- | --- | --- |
| List / search / discovery | `GET /api/v1/registry-items` | `marketplace list|search|updates` | `RegistryClient.list` / Marketplace discovery | Same server-owned text search, filters, sort, direction, cursor and fields semantics |
| Kind capabilities | `GET /api/v1/marketplace-kinds` | `marketplace kinds` | `RegistryClient.listKinds` | `supports_install/update/uninstall`, owner resource and management path come from the server; missing metadata fails closed |
| Detail / inspect / status | `GET /api/v1/registry-items/{source::item@version}` when source qualification is required | `marketplace show|status` | `RegistryClient.get` / detail card | `item_id`, kind, version, source, provenance, compatibility, install/update state and owner extension are unchanged canonical projections |
| Validation / compatibility | `marketplace.preview` and canonical item compatibility/validation fields | `marketplace preview` | Preview UI through `RegistryClient.preview` | Clients display findings/decisions; they do not recreate validation, dependency or compatibility decisions |
| Install | `POST /api/v1/commands/marketplace.install` | `marketplace install` | `RegistryClient.install` | Same resource ref/version/source, authorization, approval, decision and idempotency boundary |
| Update / source switch | `POST /api/v1/commands/marketplace.update` | `marketplace update` | `RegistryClient.update` | Server kind/owner capability metadata decides support; version/source state remains canonical |
| Uninstall | `POST /api/v1/commands/marketplace.uninstall` | `marketplace uninstall` | `RegistryClient.uninstall` | Reverse-dependency, authorization and owner-removal decisions stay server-side |
| Configure / enable / disable | Canonical owner Control Plane after package install (for example Plugins/Models/Agents/Applications as applicable) | Owner-domain CLI commands | Owner-domain Web management surface from `management_path` | Marketplace installation is not runtime activation; no Marketplace-only configure/enable/disable lifecycle is invented |
| Deprecated | canonical `deprecated` item field/filter and update decision state | `--deprecated` and unchanged item/error output | canonical badge/filter/detail state | Deprecation is server data, never inferred from package/provider names |
| Unsupported | kind descriptor, `owner_extension.supported_operations`, `operation_state`, route availability, or canonical `unsupported_capability` error | unchanged canonical response/error | fail-closed UI state from canonical capability metadata | Clients do not guess support from semantic kind identity |
| Provider unavailable | canonical `unavailable` / HTTP 503, retryable when the distribution provider failure is transient | standard Control Plane error envelope | standard API error + unavailable/degraded presentation | Provider failure does not rewrite installed owner state or create client-specific lifecycle |
| Not found / conflict | canonical 404 / 409 envelopes, including source ambiguity and dependency conflicts | standard Control Plane envelope | standard API error | Same code/category/retryability/correlation semantics |
| Retry / idempotency | mutation idempotency key is handled by the Control Plane; mutations are not transparently replayed by clients | explicit/generated idempotency key; no automatic logical mutation retry | generated/explicit idempotency key; no automatic logical mutation retry | Retryable transport status cannot create a second install/update/uninstall |
| Observable result | Marketplace mutation result + subsequently projected canonical owner state | returned response, then normal public reads | returned response, then normal public reads | Marketplace evidence never replaces Agent/Plugin/Model/Application owner lifecycle authority |

For plugin-backed semantic kinds (Orchestrator, Executor and platform-provider extensions),
configure/enable/disable parity remains owned by the existing Plugin surface rather than a
Marketplace shadow lifecycle. `src/ai_multi_agent_platform/cli/plugins.py` and
`frontend/src/api/plugins.ts` both call `plugin.configure|enable|disable` through
`/api/v1/commands/*`; `tests/integration/security/test_cli_and_approval.py`,
`frontend/src/api/plugins.test.ts`, `tests/integration/plugins/test_plugin_control_plane.py`
and `tests/integration/plugins/test_marketplace_owner_handlers.py` retain exact-payload,
authorization/approval, owner-state and Hermes activation evidence.

The shared `canonical-marketplace.json` fixture is consumed by both Python CLI tests and the
TypeScript Web client tests. It covers a successful install followed by canonical state reread,
canonical/source-qualified identity, pagination/filter/sort, authorization failure, validation
failure, not-found, conflict, unsupported capability and provider-unavailable semantics. The
existing #1174/#1221 integration/browser tests remain the owner-domain evidence for actual
dependency decisions, approval enforcement, source ambiguity, future kinds, Application
update fail-closed behavior, Agents/Teams, plugin-backed provider kinds and Hermes lifecycle
separation.

### Unsupported / deprecated and provider leakage

Web lifecycle controls now fail closed when authoritative kind capability metadata is absent.
For kind-handler routes, the owner handler must also advertise the operation; the Web client no
longer contains an Application-specific lifecycle exception or route-derived fallback authority.
CLI sends the requested canonical command and lets the Control Plane return the same
`unsupported_capability` decision rather than maintaining a second support table.

A Marketplace `item_id` is provider-neutral. A source-qualified
`<source>::<item>@<version>` reference exists only to disambiguate catalog source and version.
Source/publisher/repository/provenance and preview provider evidence remain metadata and never
become the owner resource ID or lifecycle state. In particular, #1221's Hermes acceptance keeps
the private `HERMES_ADAPTER_ID` out of the Marketplace item identity/candidate description;
installing the Orchestrator package does not register/enable that runtime until the canonical
Plugin owner is configured and enabled.

Intentional unsupported cases are not #1236 blockers: current built-in descriptors explicitly
disable first-class Marketplace install/update/uninstall for Workflow and Template, disable all
automatic lifecycle for Documentation, and disable Application update. Deployment-specific
missing handlers/providers may reduce availability further and must remain explicit.

## Maintained shared fixture contract

The cross-client fixture set under `frontend/src/api/__fixtures__/` is intentionally consumed by both Python CLI tests and TypeScript Web-client tests. It currently proves:

1. identical Task / Run / Result identity and state;
2. identical Task list pagination payload;
3. equivalent `limit`, `cursor`, `sort`, `direction`, `q`, `filter[field]` and `fields` query semantics;
4. equivalent canonical authorization-error category/status/retryability semantics;
5. use of the versioned `/api/v1` resource paths from both clients;
6. identical public Task/Run lifecycle mutation routes for queue/start/cancel/retry and Run cancellation;
7. canonical Task deep links resolve back through the public API;
8. repeated Web reads observe refreshed server state rather than retaining client-owned lifecycle state;
9. POST lifecycle mutations carry an idempotency key and are not client-retried even when a returned canonical error is retryable;
10. validation, invalid-cursor, unauthenticated, forbidden, approval-required, not-found, conflict, unavailable, retryable-backend and non-retryable-backend responses preserve canonical HTTP status, code, category, retryability and safe details across Web and CLI.

These fixtures are evidence, not a second schema. The OpenAPI / Control Plane contract remains authoritative.

## Maintained browser-level first-run evidence

The #1164 browser architecture is reused directly rather than duplicated. `frontend/tests/browserFirstRun.browser.mjs` is the maintained clean-install Chromium path and is executed by the normal frontend CI job. The #1236 assertions now extend that same flow in both directions:

1. browser bootstrap/session setup, logout/login and incomplete/completed reload behavior remain on the canonical auth/session boundary;
2. browser-created Project and Workspace responses come from public `/api/v1` mutations, and their returned canonical IDs are re-read through the matching public resources;
3. the official multi-agent mutation is observed at `/api/v1/commands/onboarding.run-multi-agent-golden-path` with an idempotency key rather than through a private backend path;
4. the browser-returned Task, Plan, Step, Run, Result, Artifact, Verification and specialized Agent identities are re-resolved through public Control Plane resources and checked against the browser projection's lifecycle/linkage state;
5. rendered Task, Run, Result and Artifact deep links contain the exact canonical IDs returned by the server;
6. an unavailable local model fails closed in the maintained UI, produces an actionable safe error and does not create a shadow/partial Task;
7. the existing standard Agent catalog bootstrap step returns Agent Team keys that are matched to canonical `/api/v1/agent-teams` resources and re-resolved by ID;
8. a separate Task created and cancelled through the public API is then opened/reloaded in the maintained Web detail route; the page observes the server's newer `cancelled` state and revision rather than retaining a Web-owned snapshot.

Authorization/approval policy itself remains server-owned. `J-web` retains canonical unauthenticated/denied/approval-required presentation coverage while the security-owned integration tests exercise the actual authorization/approval boundary; the first-run browser flow does not duplicate that policy implementation.


## Maintained executable registration

The fast platform-conformance profile keeps this behavior claim-blocking rather than documentation-only:

- `J-cli` runs shared Task/Run/Result **and Marketplace** parity, fixture-level query/error parity, a live CLI ↔ public API pagination/filter/sort/cursor comparison, shared core lifecycle-route parity, Marketplace install→read parity, mutation idempotency/no-auto-retry, a real bidirectional CLI ↔ public API Task-state test, canonical authorization denial, approval-required → approved retry behavior, and the maintained CLI assertion for the official first-run command identity.
- `J-web` runs the shared canonical-state parity suite (including Marketplace list/install/error parity, core lifecycle-route, mutation idempotency/no-auto-retry, deep-link and reload contracts), Marketplace fail-closed capability presentation, canonical frontend error presentation, and the maintained Web onboarding assertion for the same official first-run command identity.

The security tests are intentionally reused instead of reimplementing authorization policy in a client-specific conformance harness. The client under test receives the same server-owned outcome and may only render or transport it.

## Dependency-aware closure

The Marketplace/browser dependencies that previously blocked final #1236 acceptance are now
consumed by the integrated M3 state:

- #1174 provides the authoritative unified Marketplace core;
- #1221 provides Agents, Agent Teams, Orchestrators and platform-provider semantic kinds;
- #1164 provides the maintained browser-first clean-install E2E and canonical first-run evidence;
- #1271 integrated those slices together with the dependency-independent #1236 baseline.

This combined conformance document now retains both sides of the final acceptance evidence: the
maintained #1164 browser/first-run path and the #1174/#1221 Marketplace/Registry/provider path.
The final #1236 coordination pass should rerun the registered client/conformance suites on the
integrated collection branch and then make the issue-level closure decision. #747 can consume the
retained `J-cli`, `J-web` and browser-first evidence without introducing a second lifecycle
authority.
