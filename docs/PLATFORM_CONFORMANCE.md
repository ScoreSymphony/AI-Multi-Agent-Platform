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

The report schema is versioned as:

```text
ai-multi-agent-platform/platform-conformance/v1
```

A required scenario must pass for the selected profile to claim compatibility. An optional capability may be reported as `disabled` or `unsupported` without failing the reference single-node baseline. A required scenario that has not yet been wired into #46 is reported as `not_implemented` and blocks a release compatibility claim.

This distinction is deliberate: the runner must never make an operational-v1 claim merely because an acceptance path is absent.

## Profiles

### `fast`

The deterministic PR tier currently maintains the first executable cross-product slice:

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

The integration tier currently includes the complete fast tier and explicitly records optional capability profiles for:

- B — Hermes orchestration;
- C — Forge execution;
- E — distributed Worker/Node;
- S — optional Registry;
- X — optional Control Plane HA.

Until a concrete #46 integration path is selected/enabled, those entries are emitted as `disabled` rather than silently omitted or counted as a baseline failure. Future adapter-specific jobs should replace the disabled record with a real executable scenario and record the tested component version.

### `release`

The release tier extends integration with the remaining end-product scenarios. Required scenarios without a maintained executable #46 path are deliberately `not_implemented`, so the release profile is expected to remain **incomplete** while #46 is under construction.

Currently explicit required pending paths include:

- G — controlled failure/retry;
- I — Automation -> canonical Task;
- K — Task-centric Chat;
- L — Terminal/session path;
- M — Browser capability;
- O — Usage/resources attribution;
- P — Standard Agents/Teams configurability;
- W — practical Task management.

Optional or conditional domains such as Notifications, Templates, portable Import/Export, Registry, Repository/Git, Organization collaboration and HA remain non-blocking when their profile is disabled. Scenario Y for #384 durable Plan/Step coordination is recorded as unsupported until the corresponding coordination path is available.

## Compatibility semantics

Scenario status values:

- `pass` — the registered acceptance command succeeded;
- `fail` — an enabled acceptance command failed or could not execute;
- `disabled` — an optional capability/profile is intentionally not enabled;
- `unsupported` — an optional/conditional profile is not yet available in this environment;
- `not_implemented` — a required #46 acceptance path has not yet been registered.

Report compatibility values:

- `compatible` — every required scenario passed and no enabled optional scenario failed;
- `incompatible` — at least one enabled scenario failed;
- `incomplete` — no enabled scenario failed, but at least one required scenario has no passing acceptance result;
- `not_claimed` — used at scenario level for disabled/unsupported/not-yet-implemented paths.

## Evidence model

Every report records:

- conformance schema version;
- selected conformance and deployment profile;
- platform Git commit when a Git checkout is available;
- installed platform package/release version when available;
- adapter/provider/plugin version collections;
- stable scenario ID and owning issue/subsystem;
- pass/fail/availability result;
- duration;
- command output tail and failure category when applicable;
- canonical resource IDs and evidence references when a scenario exposes them.

The current first slice leaves canonical resource ID/evidence collections empty for subsystem tests that do not yet export them. They are explicit empty collections rather than fabricated identifiers. As scenarios become true cross-layer fixtures, #46 should populate those fields with Task/Run/Result/Artifact/Verification IDs and retained trace/log/artifact references.

## Architecture invariants

`tests/test_issue_46_architecture_invariants.py` currently automates two baseline invariants:

1. canonical `contracts`, `domain` and `kernel` source must not import platform adapter implementations or Hermes/Forge/LiteLLM/MCP runtime packages;
2. optional backend packages such as LiteLLM/MCP/provider SDKs must not become mandatory platform runtime dependencies.

Additional #46 invariants should be added here as they can be checked reliably without encoding brittle implementation details.

## CI tiers

The repository should treat conformance as three different cost/coverage tiers rather than one giant permutation matrix:

1. **Fast PR** — deterministic local/reference components, architecture invariants and critical lifecycle/security/verification checks.
2. **Integration** — real optional adapters/services where configured, distributed fixtures and richer cross-domain paths.
3. **Release acceptance** — representative operational, recovery, portability, security and product-path evidence required for the compatibility claims made by that release.

The first implementation wires only the fast tier into normal PR/main CI. Integration and release remain explicit CLI profiles while their scenario matrix is being completed.

## Relationship to #252

#252 remains the usable single-node prototype gate and keeps its own focused profile/report schema. #46 builds on that evidence but does not replace or broaden #252 into the operational-v1 matrix.

In particular, the #46 restart scenario calls the existing #252 persistence profile rather than copying its Memory/Verification/first-task reconstruction logic into a second fixture.

## Extension rule

When another issue completes an end-product path needed by #46:

1. keep focused unit/contract/integration tests in the owning issue;
2. add one representative public/canonical end-to-end path to the appropriate #46 profile;
3. record the owning issue, deployment profile and tested adapter/provider/plugin versions;
4. preserve optionality by reporting disabled/unsupported optional profiles explicitly;
5. never declare compatibility when the required scenario did not actually pass.
