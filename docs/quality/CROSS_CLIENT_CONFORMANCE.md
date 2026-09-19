# Cross-client Web / CLI / API conformance

Issue #1236 treats the public Web, CLI and HTTP API surfaces as projections of one canonical Control Plane. The server owns lifecycle, authorization, validation, idempotency and pagination semantics; clients may adapt presentation only.

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
| Marketplace | Marketplace resources/actions | marketplace command family | Marketplace UI/client | #1174 core and #1221 cross-kind semantics are integrated; detailed Marketplace acceptance remains owned by the separate #1236 Marketplace slice |
| Diagnostics / status | manifest, health, readiness | platform status/health/doctor | setup/status surfaces | Canonical API status is source of truth |

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
10. authorization, not-found and conflict responses preserve canonical HTTP status, code, category and retryability across Web and CLI.

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

- `J-cli` runs shared Task/Run/Result parity, fixture-level query/error parity, a live CLI ↔ public API pagination/filter/sort/cursor comparison, shared core lifecycle-route parity, mutation idempotency/no-auto-retry, a real bidirectional CLI ↔ public API Task-state test, canonical authorization denial, approval-required → approved retry behavior, and the maintained CLI assertion for the official first-run command identity.
- `J-web` runs the shared canonical-state parity suite (including core lifecycle-route, mutation idempotency/no-auto-retry, deep-link and reload contracts), canonical frontend error presentation, and the maintained Web onboarding assertion for the same official first-run command identity.

The security tests are intentionally reused instead of reimplementing authorization policy in a client-specific conformance harness. The client under test receives the same server-owned outcome and may only render or transport it.

## Dependency-aware closure

The two historical external blockers for the final integrated pass are now satisfied in the M3 integration state:

- #1164 is complete and its maintained browser-first clean-install E2E is the browser evidence extended above;
- #1221 is complete and its final Agent/Agent-Team/Orchestrator/platform-provider Marketplace semantics are integrated separately;
- #1174 remains the authoritative unified Marketplace core.

This Web/browser closure does not duplicate Marketplace semantics. The final #1236 coordination pass should consume this retained browser evidence together with the separate Marketplace evidence, rerun the registered client/conformance suites on the integrated branch and then make the issue-level closure decision. #747 can consume the retained `J-cli`, `J-web` and browser-first evidence without introducing a second lifecycle authority.
