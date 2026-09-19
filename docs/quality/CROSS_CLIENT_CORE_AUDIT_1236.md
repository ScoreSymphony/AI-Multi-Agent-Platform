# #1236 final Core cross-client audit

Audit base: `integration/m3-sammelbranch` at `18d8d195984df4e2f0232bcf9171d456562c2d33`.

This document is the final **Core** audit for #1236. Marketplace semantics and real-browser /
first-run E2E remain separate audit tracks. The purpose here is not to require every domain to have
identical UI affordances. It is to prove that every V1 workflow actually exposed by more than one
client keeps one Control Plane authority, canonical identity/state, query semantics, error meaning
and mutation safety.

## Legend

- **✓** — present and canonical.
- **—** — not applicable to that operation.
- **RO** — intentionally read-only on that client in V1.
- **U** — deliberately unsupported / not advertised by the V1 owner contract.
- **NF** — system/operator path, not a normal user-facing workflow.
- **P** — deliberately partial client exposure; the canonical API owns more operations.
- **GAP→fixed** — real #1236 gap found by this audit and fixed on `test/1236-core-final`.

For **Deep Link**, CLI means that an emitted public ID can be resolved through the public API; Web
means a stable route resolves the same public ID. For **Refresh**, CLI is naturally a fresh request
per invocation; Web must explicitly re-read server state rather than preserve client-owned lifecycle
truth.

## Operation-level Core matrix

| Domain | Operation | API | CLI | Web | Same ID | Same State | Auth | Validation | 404 | 409 | Pagination | Filter | Sort | Idempotency | Deep Link | Refresh | Test |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Authentication / Session | login / current actor | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | — | — | — | ✓ mutation | — | ✓ | auth owner tests; Web session tests |
| Authentication / Session | list / renew / revoke session | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ where exposed | ✓ where exposed | ✓ where exposed | ✓ | ✓ public session ID | ✓ | CLI auth integration + Web session tests |
| Projects | list / show | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | `test_cli_status_doctor_project_workspace_and_canonical_error_output`; #1234 Web coverage |
| Projects | create | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | — | — | — | ✓ | ✓ | ✓ | same CLI/API ID-resolution test; Web Project tests |
| Workspaces | list / show | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | same CLI/API ID-resolution test; Web Project/Workspace tests |
| Workspaces | create | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | — | — | — | ✓ | ✓ | ✓ | same CLI/API ID-resolution test |
| Tasks | list / show | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | shared fixture + live CLI/API list/state parity |
| Tasks | create / queue / start / cancel / retry | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | ✓ | ✓ | shared lifecycle routes; mutation retry/idempotency tests |
| Plans | list / show / workflow projection | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | owner coordination tests + Web/CLI public reference surfaces |
| Steps | list / show / workflow projection | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ state conflicts owner-side | ✓ | ✓ | ✓ | — | ✓ | ✓ | owner coordination tests + workflow projection tests |
| Runs | list / show | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | shared Run fixture + CLI-emitted Run → API resolution |
| Runs | cancel | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | ✓ | ✓ | shared public lifecycle route test |
| Results | list / show | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | shared Result fixture + reference clients |
| Artifacts | list / show | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | canonical reference collection + #1234 Web coverage |
| Agents | list / show | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | `CLI_AGENTS.md`, Agent CP tests, Web Agent tests |
| Agents | create / update / clone / rollback / delete | ✓ advertised subset | P — no dedicated V1 mutation UX | ✓ advertised subset | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | ✓ Web/API | ✓ | owner Agent command tests; partial CLI is intentional |
| Agent Teams | list / show | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | `CLI_AGENTS.md`, Team CP tests, Web Team tests |
| Agent Teams | create / update / clone / rollback / delete | ✓ advertised subset | P — no dedicated V1 mutation UX | ✓ advertised subset | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | ✓ Web/API | ✓ | owner Team command tests; partial CLI is intentional |
| Models | list / show | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | model CP/CLI/Web tests |
| Models | enable / disable | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | ✓ | ✓ | model command tests |
| Model Providers | list / show / health | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | provider CP/CLI/Web tests |
| Model Providers | enable / disable / refresh health | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | ✓ | ✓ | provider command tests |
| Model / Provider registration / removal | U | U | U | U | — | — | — | — | — | — | — | — | — | — | — | — | deliberately absent from user-facing V1 contract |
| Tools / Capabilities | list / show | ✓ | ✓ canonical reference/extension | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | Capability CP tests + #1234 Web coverage |
| Tools / Capabilities | provider-native invocation / MCP transport admin | U | U | U | U | — | — | — | — | — | — | — | — | — | — | — | — | deliberately provider-private; not a product workflow |
| Approvals | list / show | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | CLI auth/approval integration + `ApprovalClient` tests |
| Approvals | approve / deny | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | **GAP→fixed** | — | — | — | ✓ | ✓ | ✓ | expired/non-pending CLI now returns canonical 409; Web rejection tests in J-web |
| Verification | queue / requirement / history inspection | ✓ | P via registered resource surface | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ Web/API | ✓ | Verification CP + Web tests; CLI has no separate lifecycle |
| Verification | accept / reject / request changes | ✓ | P via registered command surface | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | ✓ Web/API | ✓ | Verification owner/gate tests |
| Search | query / exact-ID discovery | ✓ | ✓ | ✓ | ✓ | derived canonical refs | ✓ | ✓ | — | — | ✓ | ✓ | ✓ | — | ✓ result links | ✓ | Search owner + CLI/Web tests |
| Automations | list / show | ✓ | P via canonical registered surface | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ Web/API | ✓ | Automation owner + Web tests |
| Automations | create / update / pause / resume / disable / test / delivery retry | ✓ advertised subset | P via canonical command surface | ✓ advertised subset | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | ✓ Web/API | ✓ | Automation owner + Web tests |
| Automation delete | U | U | U | U | — | — | — | — | — | — | — | — | — | — | — | — | owner exposes no V1 delete command |
| Nodes | list / show | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | compute integration + Web compute tests |
| Nodes | drain / undrain / maintenance | ✓ | ✓ | ✓ advertised subset | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | ✓ | ✓ | compute admin tests |
| Workers | list / show | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | compute integration + Web compute tests |
| Workers | drain / undrain | ✓ | ✓ | ✓ advertised subset | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | — | — | ✓ | ✓ | ✓ | compute admin tests |
| Worker Jobs | list / show | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | — | ✓ | ✓ | ✓ | — | ✓ | ✓ | compute clients/pages |
| Worker Jobs / scheduler reservation mutation | NF/U | NF/U | U | U | — | — | — | — | — | — | — | — | — | — | — | — | scheduler authority is server-side |
| Diagnostics / Status | manifest / health / readiness | ✓ | ✓ | ✓ | — | ✓ | health as appropriate | ✓ | — | — | — | — | — | — | — | ✓ | CLI status/doctor + Web dashboard/status tests |

## Architecture audit

The audited Core path remains:

```text
Web / CLI
   |
   v
/api/v1 Control Plane
   |
   v
canonical application/domain services
   |
   v
replaceable providers / workers / adapters
```

The following are accepted:

1. **API/Control Plane remains canonical.** The Python CLI `ControlPlaneClient` constructs only
   versioned `/api/v1` calls; Web `ApiTransport` does the same. Domain clients own request shape,
   not lifecycle truth.
2. **No CLI/Web lifecycle authority.** Task/Run mutations, Approval decisions, Model/Provider
   toggles, Automation transitions and Node/Worker administration use public resource/command
   routes. The stale-Approval precheck was the one discovered exception that changed public failure
   semantics; it is removed by this audit.
3. **No private backend mutation path.** The maintained `ARCH` profile executes
   `tests/contract/portability/test_architecture_invariants.py`, including
   `test_northbound_python_clients_do_not_import_optional_backend_implementations`.
4. **No provider-private type/ID authority.** The same architecture suite executes
   `test_canonical_core_public_types_do_not_reference_backend_private_classes` and
   `test_canonical_shaped_backend_ids_remain_namespaced_external_refs`. Provider metadata may be
   visible as metadata; it is not required to address a public resource.
5. **Client transformations do not change lifecycle semantics.** CLI confirmation and Web disabled
   controls are UX only. Server authorization/approval/state checks remain authoritative.
6. **Permissions and Approval requirements are server-owned.** Both clients preserve canonical
   authentication/authorization/approval envelopes. An Approval retry is a new explicit request
   after the exact action has been approved; neither client bypasses the gate.

## Error-semantics matrix

The shared `canonical-error-cases.json` is now consumed by both CLI and Web parity suites.

| Semantic case | HTTP | Canonical code | Category | Retryable | Client requirement |
| --- | ---: | --- | --- | --- | --- |
| validation | 400 | `invalid_request` | `validation` | false | preserve envelope/details |
| invalid cursor | 400 | `invalid_cursor` | `validation` | false | collection read remains canonical |
| unauthenticated | 401 | `unauthorized` | `authentication` | false | distinct from forbidden |
| forbidden | 403 | `forbidden` | `authorization` | false | no client fallback |
| approval required | 403 | `forbidden` + `authorization_outcome=require_approval` | `authorization` | false | preserve Approval ID/details |
| not found | 404 | `not_found` | `resource` | false | stale link/ID remains 404 |
| conflict / stale state | 409 | `conflict` | `conflict` | false | server state wins |
| unavailable | 503 | `unavailable` | `availability` | false in fixture | preserve server retryability |
| retryable backend failure | 503 | `transient_failure` | `transient` | true | safe read retry may be bounded; meaning unchanged |
| non-retryable backend failure | 502 | `backend_error` | `backend` | false | no reclassification as transient |

CLI exit mapping remains intentional: local CLI/profile/argument failures are exit 2, a canonical
Control Plane error is exit 3, transport failure is exit 4. The stale Approval case now correctly
takes exit 3 because the failure is server-owned. Web may change presentation text, but
`ControlPlaneError.status/body.code/body.category/body.retryable/body.details` remain unchanged.

## Collection semantics audit

The common Control Plane `PageQuery` boundary establishes these defaults:

- `limit=50`;
- `sort=id`;
- `direction=asc`;
- opaque cursor;
- `q` for implementation-neutral search where supported;
- `filter[field]` for owner-supported exact filters;
- comma-separated `fields`.

CLI sends the first three defaults explicitly; Web usually omits them. This is **effective semantic
parity**, because `ControlPlaneHTTP._page_query` supplies the same values when omitted. The live
CLI/API test compares page content and continuation cursor under explicit pagination/filter/sort.
The shared client fixtures compare request construction, and the new invalid-cursor case locks the
canonical 400 validation envelope in both clients.

A syntactically malformed CLI `--filter` (not `FIELD=VALUE`) remains a local CLI input error;
that is not a server filter decision. Once a filter reaches the Control Plane, owner-domain
validation remains authoritative. Unsupported owner-specific filter values are not normalized by a
client into a different meaning.

## Mutation semantics audit

- Every public CLI POST uses an `Idempotency-Key`; Web command helpers generate or forward one.
- CLI POST has exactly one automatic attempt. Web default retry policy retries safe reads only;
  POST is not automatically replayed.
- Duplicate mutation semantics therefore remain server/idempotency-store owned.
- 409 conflict is preserved for stale lifecycle state.
- Task and Run cancellation use the same public lifecycle routes.
- Approval-required → approved retry uses the same exact-action Approval boundary.
- Stale/non-pending Approval decisions now reach the Control Plane and preserve canonical 409
  instead of being converted into a local CLI error.

Safe GET retry counts are transport convenience, not lifecycle semantics. The dedicated shared
error-semantic tests set read retries to zero so that code/category/retryability comparison is not
obscured by retry timing; mutation safety is tested separately with a retryable 503 and multiple
configured retries.

## Public IDs, deep links and refresh

- The live CLI/API tests now resolve CLI-emitted **Project, Workspace, Task and Run IDs** directly
  through public `/api/v1` GET resources.
- Shared Task/Run/Result fixtures use the same public IDs in CLI and Web.
- Web deep-link tests re-extract the ID from the route and resolve it through the public API.
- Other maintained Web detail routes use canonical resource IDs; provider/database identities are
  never required for routing.
- Repeated Web Task reads are tested against a newer server revision/state. CLI invocations are
  stateless fresh reads by construction.

The separate browser/first-run track remains responsible for real-browser Task → Plan → Step → Run
→ Result/Artifact navigation and reload evidence. Marketplace source-qualified catalog references
remain outside this Core audit.

## Findings

### Real gaps closed in this audit

1. **Stale Approval decision semantics.** CLI previously rejected a non-pending Approval locally
   with exit 2 while API/Web use canonical 409 `conflict`. CLI now keeps the inspection/confirmation
   step but lets the Control Plane decide pending/expiry state.
2. **Incomplete shared Core error regression.** The shared cross-client fixtures previously covered
   authorization plus 404/409 and one retryable 503. They now cover the full Core semantic set above.
3. **Invalid cursor regression.** Both clients now have shared evidence that an invalid collection
   cursor remains canonical validation failure.
4. **CLI-emitted public ID evidence.** Project, Workspace and Run IDs now join Task in direct
   CLI-output → public-API resolution checks.
5. **#747 evidence registration.** `J-cli` now includes the Approval stale-conflict integration and
   the expanded Core error/ID tests; `J-web` now includes the actual `ApprovalClient` suite in
   addition to shared state/error presentation.

### Deliberate partial exposure, not gaps

- Agent/Agent Team mutation UX is richer in Web/API; CLI V1 intentionally guarantees canonical
  inspection and does not invent a second lifecycle.
- Verification and Automation use registered canonical resources/commands; CLI has no reason to
  duplicate every Web-specific workflow control as a bespoke command.
- Provider/model registration-removal, direct MCP transport management, scheduler reservation
  mutation and Worker Job lifecycle control are not advertised user-facing V1 workflows.
- Raw provider/database IDs and private runtime handles are not public resource locators.

### Outside this Core track

- Marketplace/Registry/provider-kind acceptance.
- Real-browser/clean-install/first-run E2E.
- Their final integrated combination with this Core branch before #1236 closure.

## Evidence consumed by #747

The maintained fast conformance profile can consume:

- `J-cli`: shared Core state/query/error/lifecycle/idempotency tests, CLI↔API state and collection
  parity, CLI public-ID resolution, canonical authorization/Approval retry, stale Approval conflict;
- `J-web`: shared canonical state/query/error/lifecycle/idempotency/deep-link/refresh tests,
  canonical error presentation and the actual Approval client rejection suite;
- `ARCH`: backend-import/type/ID isolation and canonical identity under restart/failover;
- `F`: server-owned exact-action Approval gate;
- `U`: Verification completion gate.

Marketplace/browser entries already present in the global profile are dependency evidence owned by
their separate tracks and are not re-audited here.
