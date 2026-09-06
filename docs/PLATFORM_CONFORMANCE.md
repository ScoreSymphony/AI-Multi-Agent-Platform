# Platform-wide conformance and acceptance gate

Issue #46 defines the M3 / operational-v1 conformance gate. It answers a different question from the earlier #252 usable-prototype gate:

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
  --enable-optional E,N,R,T,V,X \
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

A required scenario must pass for the selected profile to claim compatibility. An optional capability may be reported as `disabled` or `unsupported` without failing the reference single-node baseline. Once an optional scenario is explicitly enabled it becomes required for that exact deployment claim. If executable #46 evidence is not registered, the enabled scenario becomes `not_implemented` and the report is `incomplete` rather than silently compatible.

## Profiles

### `fast`

The deterministic PR tier maintains the critical local/reference cross-product slice:

| Scenario | Evidence | Owner |
| --- | --- | --- |
| A — reference baseline | authenticated single-node Task/Run/Result restart-safe smoke | #39 / #252 |
| D-model — local model | loopback OpenAI-compatible local/self-hosted ModelProvider fixture | #10 / #250 / #252 |
| D-capability — capability boundary | capability discovery/invocation contract suite | #12 |
| F — approval gate | exact-action approval and changed-payload rejection | #15 |
| H — restart/persistence | #252 persistence acceptance profile | #39 / #250 / #251 / #86 |
| J-cli — client consistency | CLI canonical Task route/fixture parity | #17 / #252 |
| J-web — client consistency | Web canonical Task route/fixture parity | #17 / #395 |
| U — runtime verification | Verification independently gates completion | #86 |
| ARCH — architecture invariants | optional backend isolation + mandatory-dependency guard | #46 |

The fast tier is intentionally local/reference-only and deterministic. It requires no paid AI/API service and no Hermes, Forge, LiteLLM, Registry, distributed Worker or HA deployment.

### `integration`

The integration tier includes the complete fast tier and explicitly records optional capability profiles for:

- B — Hermes orchestration;
- C — Forge execution;
- E — distributed Worker/Node;
- S — optional Registry;
- X — optional Control Plane HA.

These entries remain `disabled` unless explicitly enabled. Enabling B or C uses a fail-closed external-profile runner: missing Hermes source/revision configuration or a missing Forge sidecar environment is a failed compatibility run, not a skipped passing test.

### `release`

The reference release tier extends integration with representative operational product paths. Every required reference-release scenario has maintained executable evidence:

| Scenario | Maintained acceptance evidence | Owner |
| --- | --- | --- |
| G — Failure/retry | controlled failed Run -> canonical failed Task -> retry Run attempt 2 -> retry/failure telemetry | #46 / #16 |
| I — Automation | one-time schedule creates a canonical Task with provenance | #18 |
| K — Task-centric Chat | message-to-Task Control Plane handoff is canonical and bidirectionally linked | #72 |
| L — Terminal | project/workspace-scoped Control Plane session create/list/get/terminate with idempotency | #73 |
| M — Browser | canonical download File/Artifact path plus policy-gated form upload through File permissions | #74 |
| O — Usage/resources | Task/Run/Executor accounting is canonically attributed, idempotent and aggregated | #76 |
| P — Standard Agents/Teams | starter catalog lifecycle uses the real Control Plane bootstrap/clone path | #77 |
| W — Task management | priority/deadline/not-before, assignment and dependency semantics stay canonical metadata | #88 |

G is intentionally one coherent test rather than two unrelated assertions: a real canonical Run fails through the lifecycle backend, the Task becomes failed, `retry_task()` creates a distinct second Run with `attempt == 2`, canonical history contains the failed and retry events, and the Observability event provider emits exactly one `platform.run.retries` metric for that retry.

## Optional compatibility evidence

The following optional scenarios now have maintained executable #46 evidence and can be promoted from `disabled` to claim-blocking with `--enable-optional`:

| Scenario | Representative evidence |
| --- | --- |
| B — Hermes | real pinned Hermes `/v1/runs` API compatibility test through the platform adapter |
| C — Forge | real execution-only Forge Rust sidecar integration test |
| E — Distributed Worker | authorization context + canonical terminal result identity + correlated safe distributed telemetry |
| N — Notifications | recipient scope, authenticated anti-spoofing, idempotent inbox commands and replay-safe event projection |
| R — Import/export | package integrity, secret/runtime-state exclusion, successful import and rollback on failed import |
| T — Repository/Git | exact repository revision -> canonical Workspace/Run -> change Artifact/commit provenance and retry identity |
| V — Organizations | membership suspension/removal plus resource sharing/revocation and cross-organization isolation |
| X — HA | stale-leader fencing, promotion reconciliation preserving Worker identity and duplicate-command replay without duplicate Task/Run |

The following remain deliberately unavailable as compatibility claims:

- Q — Templates: #78 is reopened and still has unresolved authorization/compatibility/rollback integration gaps;
- S — Registry: #81 is reopened and still has unresolved deployment-grade registry/install/trust/update gaps;
- Y — durable Plan/Step coordination: remains unsupported until the #384 coordination path is available.

Explicitly enabling Q, S or Y therefore blocks compatibility with `not_implemented`; it does not convert unfinished subsystem work into a conformance claim.

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

A `compatible` report applies only to its explicit deployment profile and enabled scenario set. A compatible reference release does not imply Hermes, Forge, distributed Worker, Registry or HA compatibility. Conversely, enabling an optional profile changes that scenario to `required=true` in the report, so a failed or missing path cannot be hidden behind optionality.

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

The current aggregator leaves canonical resource ID/evidence collections empty for subsystem tests that do not yet export them. They are explicit empty collections rather than fabricated identifiers. As scenarios become richer cross-layer fixtures, #46 should populate those fields with Task/Run/Result/Artifact/Verification IDs and retained trace/log/artifact references.

## Architecture invariants

`tests/test_issue_46_architecture_invariants.py` currently automates two baseline invariants:

1. canonical `contracts`, `domain` and `kernel` source must not import platform adapter implementations or Hermes/Forge/LiteLLM/MCP runtime packages;
2. optional backend packages such as LiteLLM/MCP/provider SDKs must not become mandatory platform runtime dependencies.

Additional #46 invariants should be added as they can be checked reliably without encoding brittle implementation details.

## CI tiers

The repository treats conformance as three different cost/coverage tiers rather than one giant permutation matrix:

1. **Fast PR** — deterministic local/reference components, architecture invariants and critical lifecycle/security/verification checks.
2. **Integration** — explicitly enabled optional adapters/services, distributed fixtures and richer cross-domain paths.
3. **Release acceptance** — representative operational, recovery, portability, security and product-path evidence required for the compatibility claims made by that release.

`.github/workflows/platform-conformance.yml` retains separate machine-readable reports for:

- `conformance-fast`;
- `conformance-release` for the reference single-node release claim;
- `conformance-extended-reference`, which additionally enables E/N/R/T/V/X and therefore treats all six as required.

Hermes and Forge retain their real upstream/sidecar setup in adapter-specific integration jobs; their conformance activation is valid only after those external preconditions are satisfied. The default reference jobs never install or require either runtime.

## Relationship to #252

#252 remains the usable single-node prototype gate and keeps its own focused profile/report schema. #46 builds on that evidence but does not replace or broaden #252 into the operational-v1 matrix.

In particular, the #46 restart scenario calls the existing #252 persistence profile rather than copying its Memory/Verification/first-task reconstruction logic into a second fixture.

## Extension rule

When another issue completes an end-product path needed by #46:

1. keep focused unit/contract/integration tests in the owning issue;
2. add one representative public/canonical end-to-end path to the appropriate #46 profile;
3. record the owning issue, deployment profile and tested adapter/provider/plugin versions;
4. preserve optionality by leaving the scenario disabled until the caller explicitly claims that profile;
5. make an explicitly enabled scenario required for that exact claim;
6. never declare compatibility when the required scenario did not actually pass.
