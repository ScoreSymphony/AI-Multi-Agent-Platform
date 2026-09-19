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
| Authentication/session bootstrap | auth/session resources and canonical HTTP auth errors | profile/auth commands call Control Plane | browser auth/session boundary uses ApiTransport | Existing auth tests; final browser parity depends on #1164 |
| Projects / Workspaces | `/projects`, `/workspaces` | list/show/create via canonical client | ControlPlaneClient project/workspace methods | Route/query parity architecture present; representative matrix coverage tracked here |
| Task lifecycle | `/tasks`, queue/start/cancel/retry actions | task commands use only `/api/v1` | ControlPlaneClient task methods | Shared Task fixture plus list/query/error parity tests |
| Plan / Step progress | plan-coordination / workflow projections | task workflow commands | workflow progress client/UI | Shared workflow fixture already exercised by Web; final end-to-end browser evidence depends on #1164 |
| Run status / cancellation | `/runs`, task-run cancellation action | run commands | ControlPlaneClient run methods | Shared Run fixture; lifecycle mutation remains server-owned |
| Result / Artifact inspection | canonical result/artifact resources | result/reference inspection | canonical reference/detail clients | Shared Result fixture; canonical IDs resolve through public API |
| Agents / Agent Teams | agent/team resources and commands | canonical resource/extension commands | typed agent/team clients | Existing Control Plane coverage; Marketplace projection awaits #1221 |
| Models / providers | model/provider resources | model/model-provider commands | typed model clients/pages | Same Control Plane resources; provider-private IDs must not leak |
| Tools / Capabilities | capability resources/invocation policy | canonical capability/reference paths | typed capability client/pages | Existing capability conformance; no client-owned invocation authority |
| Approvals | approval resources/actions | canonical approval commands | ApprovalClient | Existing approval policy tests; permissions remain server-owned |
| Verification | verification resources/actions | canonical resource commands | VerificationClient | Existing verification gate; completion cannot be client-bypassed |
| Search | `/search` query contract | typed search command | SearchPage / ControlPlaneClient.search | Shared query contract; authorization filtering remains server-owned |
| Automations | automation resources/actions | canonical extension/domain commands | AutomationClient | Existing automation acceptance evidence; lifecycle remains canonical |
| Workers / Nodes | worker/node resources/actions | compute commands | compute clients/pages | Supported-state projection only; scheduler/worker authority is server-side |
| Marketplace | Marketplace resources/actions | marketplace command family | Marketplace UI/client | Core semantics available from #1174; final cross-kind parity awaits #1221 |
| Diagnostics / status | manifest, health, readiness | platform status/health/doctor | setup/status surfaces | Canonical API status is source of truth |

## Maintained shared fixture contract

The cross-client fixture set under `frontend/src/api/__fixtures__/` is intentionally consumed by both Python CLI tests and TypeScript Web-client tests. It currently proves:

1. identical Task / Run / Result identity and state;
2. identical Task list pagination payload;
3. equivalent `limit`, `cursor`, `sort`, `direction`, `q`, `filter[field]` and `fields` query semantics;
4. equivalent canonical authorization-error category/status/retryability semantics;
5. use of the versioned `/api/v1` resource paths from both clients;\n6. identical public Task/Run lifecycle mutation routes for queue/start/cancel/retry and Run cancellation;\n7. canonical Task deep links resolve back through the public API;\n8. repeated Web reads observe refreshed server state rather than retaining client-owned lifecycle state;\n9. POST lifecycle mutations carry an idempotency key and are not client-retried even when a returned canonical error is retryable;\n10. authorization, not-found and conflict responses preserve canonical HTTP status, code, category and retryability across Web and CLI.

These fixtures are evidence, not a second schema. The OpenAPI / Control Plane contract remains authoritative.


## Maintained executable registration

The fast platform-conformance profile keeps this behavior claim-blocking rather than documentation-only:

- `J-cli` runs shared Task/Run/Result parity, fixture-level query/error parity, a live CLI ↔ public API pagination/filter/sort/cursor comparison, shared core lifecycle-route parity, mutation idempotency/no-auto-retry, a real bidirectional CLI ↔ public API Task-state test, canonical authorization denial, and approval-required → approved retry behavior.
- `J-web` runs the shared canonical-state parity suite (including core lifecycle-route, mutation idempotency/no-auto-retry, deep-link and reload contracts) plus canonical frontend error presentation, including the distinction between unauthenticated, denied and approval-required outcomes.

The security tests are intentionally reused instead of reimplementing authorization policy in a client-specific conformance harness. The client under test receives the same server-owned outcome and may only render or transport it.

## Dependency-aware closure

#1236 can be implemented incrementally, but final closure is intentionally gated on:

- #1164 for maintained browser-level first-run evidence through the real frontend boundary;
- #1221 for the accepted final Marketplace Agent/Team/Orchestrator/provider semantics;
- #1174 is already complete and its unified Marketplace core semantics can be consumed now.

After those dependencies land, the final #1236 pass must rerun the matrix against the integrated `main`, add any missing Marketplace/browser rows to executable conformance, and leave #747 with retained product-coherence evidence.
