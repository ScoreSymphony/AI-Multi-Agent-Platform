# Platform-wide conformance and acceptance gate

Issue #46 defines the M3 / operational-v1 conformance gate. It answers whether the accumulated platform behaves as one coherent, architecture-compliant system and whether every compatibility claim has retained acceptance evidence.

The conformance layer is an aggregator, not a second platform implementation. A failing scenario must be fixed at its owning contract/integration boundary rather than bypassed in the runner.

## Command

```text
platform-conformance --profile fast --json-report conformance-fast.json
platform-conformance --profile integration --json-report conformance-integration.json
platform-conformance --profile release --json-report conformance-release.json
```

Maintained optional claims are opt-in and claim-blocking. For example:

```text
platform-conformance \
  --profile release \
  --deployment-profile reference-single-node-extended \
  --enable-optional E,N,Q,R,S,T,V,X,Y \
  --json-report conformance-extended-reference.json
```

The maintained external-adapter profile is Hermes:

```text
platform-conformance \
  --profile integration \
  --deployment-profile hermes-pinned \
  --enable-optional B \
  --adapter-version hermes-agent=<tested-revision> \
  --json-report conformance-hermes.json
```

`--adapter-version`, `--provider-version` and `--plugin-version` accept repeatable `NAME=VERSION` values. `--enable-optional` may be repeated or contain comma-separated scenario IDs.

The report schema is `ai-multi-agent-platform/platform-conformance/v1`.

A required scenario must pass for the selected profile to claim compatibility. An optional capability may be `disabled` or `unsupported` without failing the reference baseline. Once an optional scenario is explicitly enabled it becomes required. If no maintained command exists, activation yields `not_implemented` rather than a silent pass.

## Fast profile

The deterministic PR tier is local/reference-only and requires no paid AI/API service or optional external adapter.

| Scenario | Evidence | Owner |
| --- | --- | --- |
| A | authenticated single-node Task/Run/Result restart-safe smoke | #39 / #252 |
| D-model | loopback OpenAI-compatible local/self-hosted ModelProvider fixture | #10 / #250 / #252 |
| D-capability | capability discovery/invocation contract suite | #12 |
| D-vertical | authenticated AgentRun -> local model -> canonical capability -> ReferenceExecutor -> exact Worker/Node/Workspace | #46 / #10 / #12 / #7 / #14 |
| MA | maintained external-orchestrator/executor-free #889 multi-agent golden path with canonical provenance | #889 / #46 |
| F | exact-action authorization/approval gate | #15 |
| H | restart/recovery without duplicate dispatch | #46 |
| J-cli / J-web | shared canonical state through versioned Control Plane routes | #17 / #395 / #46 |
| U | runtime Verification completion gate and bounded repair | #86 |
| ARCH | backend-neutral architecture invariants | #46 |

`MA` is the maintained removal-gate path for #991: the normal single-node multi-agent runtime preserves Plan/Step/Run/AgentRun, Handoff, ContextBundle, Result and Verification provenance without Hermes or the removed Forge runtime becoming hidden owners.

## Integration profile

The integration tier includes the fast tier and optional capability slots for:

- B — Hermes orchestration;
- E — distributed Worker/Node;
- S — optional Registry;
- X — optional Control Plane HA.

Hermes B has maintained external evidence and fails closed when its exact source/revision environment is not prepared.

### Retired Scenario C

Scenario identifier C historically represented the Forge execution compatibility profile. Issue #991 removed the first-party Forge adapter, HTTP transport, sidecar CI and maintained external acceptance command after the #889/#46 removal gates passed. C is therefore **not a current compatibility claim**. If an older caller explicitly requests C, the conformance layer has no maintained command and reports it as `not_implemented`/incomplete rather than executing or implying Forge support. New deployment profiles must not enable C.

Historical Forge evidence remains in ADR/provenance/audit documents only.

## Release profile

The release tier extends integration with required operational paths including backup/restore, upgrade lifecycle, deterministic evaluation and the full authenticated reference vertical.

| Scenario/check | Maintained acceptance evidence | Owner |
| --- | --- | --- |
| G | controlled failure -> canonical retry attempt 2 -> retry/failure telemetry | #46 / #16 |
| I | Automation creates a canonical Task | #18 |
| K | Chat message-to-Task canonical handoff | #72 |
| L | authorized Workspace-bounded terminal/session path | #73 |
| M | Browser through replaceable Capability/File/security boundaries | #74 |
| O | canonical usage/resource attribution | #76 |
| P | standard Agents/Teams lifecycle | #77 |
| W | canonical task-management semantics | #88 |
| Z | parallel coding integration with validation/repair | #872 / #46 |
| REL-BACKUP | replacement-machine restore preserves canonical identity/history | #40 |
| REL-UPGRADE | preflight, migration history, backup and resume semantics | #41 |
| REL-EVAL | deterministic no-paid-service regression gate | #19 |
| REL-VERTICAL | authenticated full reference slice through Agent/Model/Capability/Executor/Worker/Workspace/File/Artifact/Verification/API | #46 |

## Maintained optional evidence

The maintained optional evidence registry contains:

| Scenario | Representative evidence |
| --- | --- |
| B | pinned Hermes API compatibility through the platform adapter |
| E | distributed authorization, result identity and telemetry |
| N | recipient-scoped Notifications and replay-safe projection |
| Q | Template authorization/dependencies/compatibility/compensation/revision stability |
| R | server-owned import/export mapping, integrity and rollback |
| S | optional Registry and plugin lifecycle |
| T | repository revision -> Workspace/Run -> change provenance |
| V | Organization sharing/isolation and historical provenance |
| X | HA stale-leader fencing and promotion reconciliation |
| Y | durable Plan/Step coordination, restart/recovery and repair |

Forge/C is deliberately absent from this maintained registry after #991.

## Compatibility semantics

Scenario statuses are `pass`, `fail`, `disabled`, `unsupported` and `not_implemented`. Report-level results are `compatible`, `incompatible`, `incomplete` and `not_claimed`.

A compatible report applies only to its explicit deployment profile and enabled scenario set. A compatible reference release does not imply compatibility for any disabled/retired external backend. Enabling an optional maintained profile makes it required for that exact report.

## Evidence model

Reports retain the conformance schema, deployment profile, platform revision/release, adapter/provider/plugin versions, stable scenario IDs, ownership, requirement flags, result, duration, command output/failure category and any emitted canonical resource/evidence references. Runtime evidence is recorded only when a maintained acceptance path actually observes it; the runner does not fabricate IDs.

## Architecture invariants

The architecture invariant suite enforces that canonical contracts/domain/kernel and northbound client-domain code do not depend on concrete backend adapters; public canonical types do not expose provider-private classes; canonical Task/Run identity survives replaceable/distributed execution; and optional provider packages do not become mandatory runtime dependencies.

Historical backend-private field names may remain in portability/import validation specifically so old exported state cannot smuggle provider-private runtime authority into canonical state. Such rejection rules are not active backend dependencies.

## CI tiers

CI separates:

1. fast deterministic local/reference evidence;
2. explicitly enabled integration profiles;
3. release acceptance and recovery/portability/security evidence.

The normal CI and release claims no longer build, launch or test a Forge sidecar. Hermes keeps its own pinned compatibility path; other optional profiles retain only the evidence named above.
