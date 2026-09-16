# Platform-wide conformance and acceptance gate

The M3 / operational-v1 platform-conformance gate, originally tracked under issue #46, answers a different question from the earlier #252 usable-prototype gate:

> Does the accumulated platform behave as one coherent, architecture-compliant system, and can every compatibility claim be backed by retained acceptance evidence?

The conformance layer is an **aggregator**, not another implementation of the platform. It reuses canonical subsystem tests and acceptance paths owned by their source issues. A failing scenario must be fixed at the owning contract/integration boundary rather than bypassed inside the conformance runner.

## Command

From a repository checkout with development and frontend dependencies installed:

```text
platform-conformance --profile fast --json-report conformance-fast.json
platform-conformance --profile integration --json-report conformance-integration.json
platform-conformance --profile release --json-report conformance-release.json
```

Optional compatibility claims are opt-in and claim-blocking:

```text
platform-conformance \
  --profile release \
  --deployment-profile reference-single-node-extended \
  --enable-optional E,N,Q,R,S,T,V,X,Y \
  --json-report conformance-extended-reference.json
```

External adapter profiles are enabled only in a prepared real environment. Tested component revisions can be retained in the same report:

```text
platform-conformance \
  --profile integration \
  --deployment-profile hermes-pinned \
  --enable-optional B \
  --adapter-version hermes-agent=<tested-revision> \
  --json-report conformance-hermes.json
```

`--adapter-version`, `--provider-version` and `--plugin-version` accept repeatable `NAME=VERSION` values. `--enable-optional` may be repeated or contain comma-separated scenario IDs.

The report schema is versioned as:

```text
ai-multi-agent-platform/platform-conformance/v1
```

A required scenario must pass for the selected profile to claim compatibility. An optional capability may be reported as `disabled` or `unsupported` without failing the reference single-node baseline. Once an optional scenario is explicitly enabled it becomes required for that exact deployment claim. If executable platform-conformance evidence is not registered, the enabled scenario becomes `not_implemented` and the report is `incomplete` rather than silently compatible.

## Profiles

### `fast`

The deterministic PR tier maintains the critical local/reference cross-product slice:

| Scenario | Evidence | Owner |
| --- | --- | --- |
| A — reference baseline | authenticated single-node Task/Run/Result restart-safe smoke | #39 / #252 |
| D-model — local model | loopback OpenAI-compatible local/self-hosted ModelProvider fixture | #10 / #250 / #252 |
| D-capability — capability boundary | capability discovery/invocation contract suite | #12 |
| D-vertical — local model + distributed capability | authenticated AgentRun -> real loopback OpenAI-compatible model tool call -> pinned `tool.echo@1.0` -> canonical CapabilityInvoker/ToolInvocation -> DistributedExecutorEchoProvider -> ReferenceExecutor -> exact Worker/Node while preserving the root Run and Workspace/Snapshot binding | platform conformance / #10 / #12 / #7 / #14 |
| MA — reference multi-agent baseline | maintained external-orchestrator/executor-free single-node #889 golden path: canonical multi-Step Plan with parallel roots/fan-in -> exact Agent revisions -> Handoffs -> ContextBundles -> Result -> exact Verification -> accepted Task, with persisted provenance across every boundary | #889 / platform conformance |
| F — approval gate | exact-action approval and changed-payload rejection | #15 |
| H — restart/recovery | crash after backend accept -> process reconstruction -> same running canonical Run with no duplicate dispatch, plus queued/pre-accept/orphaned recovery classification | platform conformance / canonical kernel recovery |
| J-cli — client consistency | CLI reads shared canonical Task/Run/Result fixtures through versioned Control Plane routes | #17 / platform conformance |
| J-web — client consistency | Web reads the same canonical Task/Run/Result fixtures through the same versioned API routes | #17 / #395 |
| U — runtime verification | Verification gates completion, binds exact revisions, works deterministically without an LLM, enforces reviewer independence and keeps repair loops bounded and auditable | #86 |
| ARCH — architecture invariants | canonical/backend isolation, AST-resolved northbound Python client backend isolation, backend-private public-type guard and platform-owned Task/Run identity preserved through distributed restart/failover | platform conformance |

The fast tier is intentionally local/reference-only and deterministic. It requires no paid AI/API service and no Hermes, removed Forge runtime, LiteLLM, Registry, remote distributed deployment or HA service. D-vertical does instantiate an in-process local Worker/Node fixture so the canonical Executor/Worker boundary is continuously exercised without claiming the optional distributed deployment profile.

`MA` is the maintained platform-conformance acceptance registration for the #889 reference multi-agent runtime rather than a second demo implementation. This registration originated under issue #46. Its conformance command executes `test_reference_multi_agent_golden_path_persists_complete_canonical_provenance` directly. The test constructs the ordinary `build_single_node_deployment` path with local `FakeModelProvider` execution and the reference Context orchestrator, then proves persisted Task -> Plan -> Step -> Run -> AgentRun -> Handoff -> ContextBundle -> Result -> Verification -> accepted Task lineage. Hermes stays disabled and the removed Forge runtime is absent rather than acting as a hidden fallback owner.

### `integration`

The integration tier includes the complete fast tier and explicitly records maintained optional capability profiles for:

- B — Hermes orchestration;
- E — distributed Worker/Node;
- S — optional Registry;
- X — optional Control Plane HA.

These entries remain `disabled` unless explicitly enabled. Enabling B uses a fail-closed external-profile runner: missing Hermes source/revision configuration is a failed compatibility run, not a skipped passing test.

Scenario ID C historically represented Forge execution. After #991 removed the first-party Forge adapter, HTTP transport and sidecar compatibility lane, C has no maintained acceptance command and is **not a current compatibility claim**. If an older caller explicitly enables C, activation fails closed as `not_implemented`/`incomplete`; new deployment profiles must not enable it.

### `release`

The reference release tier extends integration with representative operational product paths. Every required reference-release scenario has maintained executable evidence:

| Scenario | Maintained acceptance evidence | Owner |
| --- | --- | --- |
| G — Failure/retry | controlled failed Run -> canonical failed Task -> retry Run attempt 2 -> retry/failure telemetry | platform conformance / #16 |
| I — Automation | one-time schedule creates a canonical Task with provenance | #18 |
| K — Task-centric Chat | message-to-Task Control Plane handoff is canonical and bidirectionally linked | #72 |
| L — Terminal | project/workspace-scoped Control Plane session create/list/get/terminate with idempotency | #73 |
| M — Browser | canonical download File/Artifact path plus policy-gated form upload through File permissions | #74 |
| O — Usage/resources | Task/Run/Executor/model/Worker/Node usage is canonically attributed, provider-neutral resource gauges are retained and unavailable measurements are explicit rather than fabricated | #76 |
| P — Standard Agents/Teams | catalog discovery is non-installing; Control Plane bootstrap/clone/delete supports independent user Agent and AgentTeam customization/removal while bundled definitions remain protected | #77 |
| W — Task management | priority/deadline/not-before, assignment and dependency semantics stay canonical metadata; bulk updates preflight authorization and urgent priority cannot bypass distributed Worker admission | #88 |
| Z — Parallel coding integration | independent coding Steps fan out concurrently, dependent work waits, integration requires exact validation/authorization, and conflicts require bounded canonical repair with fresh combined validation | #872 / platform conformance |

G is intentionally one coherent test rather than two unrelated assertions: a real canonical Run fails through the lifecycle backend, the Task becomes failed, `retry_task()` creates a distinct second Run with `attempt == 2`, canonical history contains the failed and retry events, and the Observability event provider emits exactly one `platform.run.retries` metric for that retry.

#### Required release-lifecycle evidence

The release tier also has explicit claim-blocking lifecycle and cross-layer checks required by the platform release-conformance matrix, originally tracked under issue #46. These are not additional product scenarios; `REL-*` IDs distinguish release-level evidence from the product-surface scenario IDs:

| Release check | Maintained acceptance evidence | Owner |
| --- | --- | --- |
| REL-BACKUP | functioning single-node deployment -> quiesced checksummed backup -> clean replacement data root -> normal restore recovery -> preserved canonical user/Task/Run identity and `ready_for_service=true` | #40 |
| REL-UPGRADE | controlled previous-schema fixture -> preflight -> recorded migration -> target version state, plus verified-backup enforcement for forward-only migration and explicit resume after interrupted restart-safe migration | #41 |
| REL-EVAL | checked-in deterministic no-paid-service suite/policy/baseline executed by the real #19 CI regression gate | #19 |
| REL-VERTICAL | authenticated HTTP -> Control Plane -> canonical Task/Run -> AgentRuntime -> local ModelRuntime -> canonical Capability/ToolInvocation -> ReferenceExecutor -> Worker/Node -> exact remote Workspace materialization -> canonical File + distinct Artifact -> exact Verification evidence -> accepted Task -> canonical API/timeline/observability | platform conformance |

All four are `required=true` whenever `--profile release` is selected. A release report therefore cannot be `compatible` merely because these capabilities have unit tests elsewhere; the representative acceptance commands must pass inside the same platform release claim.

`REL-VERTICAL` is the maintained **continuous full reference slice** for the canonical full-reference conformance path. The authenticated AgentRun keeps one platform-owned `task_*`/`run_*` identity while the local model requests `tool.workspace.write_artifact`; that semantic capability is pinned to a canonical `tool_invocation_*`, dispatched through the reference Executor to an exact Worker/Node and materialized back into the bound Workspace/Snapshot. Worker-produced content becomes a canonical `file_*` plus a distinct canonical `artifact_*`, is attached to the original Run/Task, is propagated through the AgentRun and becomes the exact Artifact evidence for canonical Verification before final Task acceptance. The `worker_job_*` remains a child subexecution caused by the ToolInvocation and never becomes a second Run. The completed state, Result and Verification are then read through the maintained Control Plane resources and Task timeline, where canonical lifecycle and verification telemetry are asserted. This closes the previously documented Agent/Model-versus-Executor/Worker split without making Worker, Executor or model-provider state canonical.

## Optional compatibility evidence

The following optional scenarios have maintained executable platform-conformance evidence and can be promoted from a non-claim to claim-blocking with `--enable-optional`:

| Scenario | Representative evidence |
| --- | --- |
| B — Hermes | real pinned Hermes `/v1/runs` API compatibility test through the platform adapter |
| E — Distributed Worker | authorization context + canonical terminal result identity + correlated safe distributed telemetry |
| N — Notifications | authenticated recipient scope, Task success/failure plus Approval/Verification source linkage, deduplication and replay-safe projection |
| Q — Templates | exact-source authorization, dependency/compatibility/secret blockers, guarded composite compensation, composed Single-Node integrations and composite revision stability: later published Template revisions do not alter an existing instance/reapply unless an upgrade revision is explicitly selected |
| R — Import/export | canonical Control Plane/CLI export-preview-import flow with server-owned deterministic ID mapping, File/Artifact reference remapping, checksum enforcement, conflict preview, secret/runtime-state exclusion and rollback on failed import |
| S — Registry | Registry-disabled single-node startup, configured local/offline composition sharing canonical plugin lifecycle, server-resolved preview validation and authoritative signature verification |
| T — Repository/Git | exact repository revision -> canonical Workspace/Run -> change Artifact/commit provenance and retry identity |
| V — Organizations | personal operation without Organization membership, membership suspension/removal, intentional resource sharing/revocation, explicit cross-organization authorization/isolation and preserved historical Task/Event actor provenance |
| X — HA | stale-leader fencing, promotion reconciliation preserving Worker identity and duplicate-command replay without duplicate Task/Run |
| Y — durable Plan/Step coordination | crash/restart-safe Run creation, waits/retries/fan-in, stale-fence rejection, real distributed lost-ack/cancellation reconciliation, restore/history consistency, orchestrator-replacement invariance, conservative authorized repair and explicit coordination observability |

Q, S and Y were previously unavailable while their owning implementation issues were still open. Their owning work is now complete and each has retained executable evidence. They remain optional deployment claims rather than becoming implicit requirements of the reference release profile, and therefore report `disabled` by default unless explicitly enabled.

For Q, a maintained integration regression applies a published composite revision with a pinned dependency, publishes later dependency/composite revisions, then proves default reapply keeps the original source/dependency revisions while an explicit upgrade selects the newer revision. Existing instance records and resources are not silently rewritten.

For R, the maintained public path goes through the versioned Control Plane commands and CLI rather than trusting a client-owned import plan. The server generates the import mapping, rejects a forged mapping, preserves deterministic replay/idempotency, validates File/Artifact checksums and references, reports conflicts before mutation and excludes plaintext secrets/backend-private runtime state.

For V, the enabled claim includes personal scope without a synthetic Organization, Organization/Team sharing and isolation, an explicit authorization check for requested cross-Organization sharing, immediate loss of future Membership-derived scope after suspension/removal, and unchanged historical Task ownership/Event actor provenance.

B still requires a prepared external Hermes environment. Explicit activation remains fail-closed when that external precondition is absent. Forge/C is deliberately absent from the maintained evidence registry after #991.

## Compatibility semantics

Scenario status values:

- `pass` — the registered acceptance command succeeded;
- `fail` — an enabled acceptance command failed or could not execute;
- `disabled` — an optional capability/profile is intentionally not enabled;
- `unsupported` — an optional/conditional profile is not yet available in this environment;
- `not_implemented` — a required acceptance path has not yet been registered.

Report compatibility values:

- `compatible` — every required scenario passed and no enabled optional scenario failed;
- `incompatible` — at least one enabled scenario failed;
- `incomplete` — no enabled scenario failed, but at least one required scenario has no passing acceptance result;
- `not_claimed` — used at scenario level for disabled/unsupported/not-yet-implemented paths.

A `compatible` report applies only to its explicit deployment profile and enabled scenario set. A compatible reference release does not imply Hermes, the removed Forge runtime, distributed Worker, Registry or HA compatibility. Conversely, enabling an optional maintained profile changes that scenario to `required=true` in the report, so a failed or missing path cannot be hidden behind optionality.

## Evidence model

Every report records:

- conformance schema version;
- selected conformance and deployment profile;
- platform Git commit when a Git checkout is available;
- installed platform package/release version when available;
- adapter/provider/plugin version collections;
- stable scenario ID and owning issue/subsystem;
- whether the scenario was required for the concrete claim;
- pass/fail/availability result;
- duration;
- command output tail and failure category when applicable;
- canonical resource IDs and evidence references when a scenario exposes them.

Scenarios may emit a structured runtime-evidence envelope after their maintained acceptance path has proven the resources it exercised. The conformance runner promotes those observed canonical IDs and evidence references into the versioned JSON report instead of fabricating them. `REL-VERTICAL` requires this envelope and fails closed if it is missing or malformed; its report therefore retains the concrete Task, Run, Agent, AgentRun, ToolInvocation, WorkerJob, Worker, Node, Workspace, Snapshot, File, Artifact, Result and Verification IDs together with the canonical API/timeline and observability references exercised by the slice. Subsystem tests that do not yet export runtime evidence continue to use explicit empty canonical-ID collections rather than guessed identifiers.

## Architecture invariants

`tests/contract/portability/test_architecture_invariants.py` currently automates six platform-boundary invariant families plus focused guard self-tests:

1. canonical `contracts`, `domain` and `kernel` source must not import platform adapter implementations or Hermes/Forge/LiteLLM/MCP runtime packages;
2. CLI, Chat/conversation and Terminal Python client-domain code must not import platform adapter implementations or Hermes/Forge/LiteLLM/MCP runtime packages directly; imports are resolved from the AST so relative and package-level spellings cannot bypass the guard;
3. public canonical type annotations, including quoted/forward-reference annotations, must not expose backend-private Hermes/Forge/LiteLLM/MCP classes;
4. even canonical-shaped backend Task/Run IDs remain namespaced `ExternalRef` metadata while the platform generates its own canonical Task/Run identities;
5. the real distributed lost-owner -> fence -> persisted restart -> alternate-Worker redispatch path must preserve the same platform-owned canonical Task/Run identity and correlation/causation context;
6. optional backend packages such as LiteLLM/MCP/provider SDKs must not become mandatory platform runtime dependencies.

The retained Forge names in these negative architecture guards are intentional: they prevent a removed vendor-specific runtime from leaking back into canonical layers and are not an active Forge dependency.

The Python client-domain check complements the maintained Control Plane parity scenarios; the Web client remains covered by its own API-facing integration/acceptance evidence rather than by this Python import scanner. Control Plane HA identity remains owned by optional scenario X instead of being inferred from the focused distributed-runtime invariant.

Additional platform-conformance invariants should be added as they can be checked reliably without encoding brittle implementation details.

## CI tiers

The repository treats conformance as three different cost/coverage tiers rather than one giant permutation matrix:

1. **Fast PR** — deterministic local/reference components, architecture invariants and critical lifecycle/security/verification checks, including the required `MA` reference multi-agent golden path.
2. **Integration** — explicitly enabled optional adapters/services, distributed fixtures and richer cross-domain paths.
3. **Release acceptance** — representative operational, recovery, portability, security and product-path evidence required for the compatibility claims made by that release.

`.github/workflows/conformance.yml` retains separate machine-readable reports for:

- `conformance-fast`;
- `conformance-release` for the reference single-node release claim;
- `conformance-extended-reference`, which additionally enables E/N/Q/R/S/T/V/X/Y and therefore treats all nine as required.

Both release-based jobs execute `REL-BACKUP`, `REL-UPGRADE`, `REL-EVAL` and the complete `REL-VERTICAL` automatically because those checks are required members of the release profile rather than separately enabled options.

Hermes retains its real upstream setup in an adapter-specific integration job; its conformance activation is valid only after those external preconditions are satisfied. The default reference jobs never install or require Hermes. The former Forge sidecar job and maintained Scenario C evidence were removed under #991.

## Relationship to #252

#252 remains the usable single-node prototype gate and keeps its own focused profile/report schema. The platform-wide conformance gate, historically tracked under issue #46, builds on that evidence but does not replace or broaden #252 into the operational-v1 matrix.

The H platform-conformance scenario now targets unfinished canonical Run recovery directly rather than invoking the broader #252 persistence profile. Memory, Verification and first-task reconstruction remain covered by #252 and their owning subsystem tests, while H specifically proves post-accept process reconstruction on the same Run without duplicate dispatch and deterministic classification of queued, pre-accept and orphaned work.

## Extension rule

When another issue completes an end-product path needed by platform-wide conformance:

1. keep focused unit/contract/integration tests in the owning issue;
2. add one representative public/canonical end-to-end path to the appropriate platform-conformance profile;
3. record the owning issue, deployment profile and tested adapter/provider/plugin versions;
4. preserve optionality by leaving the scenario disabled until the caller explicitly claims that profile;
5. make an explicitly enabled scenario required for that exact claim;
6. never declare compatibility when the required scenario did not actually pass.
