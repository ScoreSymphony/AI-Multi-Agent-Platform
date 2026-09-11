# Hermes Agent v0.21.1 compatibility validation

Issue: #733

Review date: 2026-09-11

## Candidate

- **Upstream:** `https://github.com/NousResearch/hermes-agent`
- **Release:** Hermes Agent v0.21.1
- **Release tag:** `v2026.9.7`
- **Annotated tag object:** `9949d0d324a3a06ac238e01dd1c2103dbca09900`
- **Exact candidate commit:** `2237be355906fbe6065ce1815711eee52b2d646e`
- **Previously accepted platform pin:** `63279301bcbdc185c1b07b98a9312eb0c862f26d`
- **License:** MIT
- **Integration model:** optional external self-hosted service behind the platform-owned `Orchestrator` and `AgentOrchestratorMapper` boundaries.

The release tag is recorded for operator traceability, but the platform pin remains the immutable commit SHA.

## Upstream delta review

GitHub's compare result from the previously accepted pin to the candidate reports the candidate as strictly ahead: **5,061 commits ahead and 0 commits behind**. This is a large upstream delta and remains classified as a high-risk dependency update even though v0.21.1 is a patch-level Hermes release.

The upstream v0.21.1 release notes describe a broad roll-up of changes across the agent loop, providers/models, MCP, delegation, desktop/application surfaces, performance and related infrastructure. The platform therefore does not infer compatibility from SemVer alone; compatibility is gated by the narrow adapter contract and real pinned-runtime conformance.

## License and provenance review

The candidate commit still carries the upstream MIT license. No Hermes source is copied, vendored or modified by this repository. The platform owns only its adapter code, configuration, tests and provenance metadata.

The governed provenance entry is `upstream/hermes-agent.yaml`. The candidate pin is accepted only together with the passing revision-bound evidence recorded below.

## Programmatic API review

The candidate still exposes the required API-server run lifecycle surface:

| Operation | Candidate surface | Platform use |
| --- | --- | --- |
| admit planning run | `POST /v1/runs` | `HermesOrchestrator.plan()` |
| reconcile run | `GET /v1/runs/{run_id}` | polling/reconciliation only |
| events | `GET /v1/runs/{run_id}/events` | available upstream, not canonical history |
| approval | `POST /v1/runs/{run_id}/approval` | deliberately not used as canonical approval authority |
| steer | `POST /v1/runs/{run_id}/steer` | not part of the baseline canonical contract |
| stop | `POST /v1/runs/{run_id}/stop` | cancellation/best-effort timeout cleanup |
| health | `GET /health` | adapter health probe |
| detailed health | `GET /health/detailed` | upstream authenticated operator surface; not consumed by the baseline adapter |

The candidate idempotency implementation retains terminal states `completed`, `failed`, `cancelled` and `interrupted`. Hermes may also expose `waiting_for_approval`; the platform continues to fail closed rather than auto-approve it.

The exact pinned runtime is additionally checked for its API-server startup key guard, bearer authentication behavior and health route. The platform adapter's configured `API_SERVER_KEY` remains a transport credential only; it does not grant canonical platform authorization.

## Compatibility matrix

| Concern | Expected platform behavior | Evidence |
| --- | --- | --- |
| admission | task objective and planner instructions map to `/v1/runs` | real pinned-runtime integration test |
| polling | `queued` / `started` / `running` remain non-terminal | adapter regression + real pinned runtime |
| success | `completed` output normalizes to provider-neutral `PlanResponse` | regression + real pinned runtime |
| failure | `failed` normalizes to canonical `BACKEND_ERROR` and preserves only safe diagnostics | v0.21.1 completion regression |
| cancellation | `cancelled` / `interrupted` normalize to canonical `CANCELLED` | version regression |
| approval | `waiting_for_approval` normalizes to canonical `FORBIDDEN` and never invokes Hermes approval | version + completion regression |
| cancel while waiting | a waiting Hermes run can be stopped without approval | completion regression |
| restart/reconciliation | a recreated adapter reconciles the same external run ID instead of admitting a replacement run | completion regression + exact pinned-runtime recreation test |
| unknown state | unknown non-running state fails closed as `BACKEND_ERROR` | version regression |
| timeout | transport/provider deadline normalizes to canonical retryable `TIMEOUT` | version regression |
| HTTP/auth errors | 400/401/403/404/409/429/5xx retain canonical error classes | version regression |
| pinned upstream authentication | strong startup key accepted, wrong bearer rejected with 401 | exact pinned-runtime completion test |
| health/startup | startup key guard and `/health` are compatible with adapter composition | exact pinned-runtime completion test |
| IDs | Hermes run IDs remain adapter-private metadata | real pinned-runtime integration + Scenario B |
| Agent/Team revisions | exact canonical revisions remain platform-owned | #8 mapper contract in Scenario B |
| model/tool bridge | explicit mapping remains fail-closed | #8 mapper contract + full CI |
| execution ownership | Hermes plans; reference executor remains canonical execution path | real pinned-runtime kernel E2E |
| disabled path | disabled adapter fails before network work | version regression + ordinary full CI |
| upstream route surface | run/status/events/approval/steer/stop plus health routes exist at exact candidate checkout | real pinned-runtime lifecycle-surface/completion tests |

## Restart and identity recovery semantics

`HermesOrchestrator` intentionally owns no canonical persistence. Restart recovery therefore consists of two separate responsibilities:

1. canonical Task/Run/Plan/Step identity is recovered from platform-owned persistence/history;
2. the stored namespaced Hermes `external_run_id` is passed back to a newly created adapter instance, which reconciles the same upstream run through `GET /v1/runs/{run_id}`.

The completion regressions recreate the adapter and prove that reconciliation/cancellation continue against the same Hermes run ID without a replacement `POST /v1/runs`. The exact pinned-runtime integration also creates a real run, recreates `HermesOrchestrator`, and reconciles that same completed upstream run while the original canonical Task ID remains unchanged in platform adapter metadata.

This is the actual current seam; Hermes does not own or reconstruct canonical identity.

## Approval, authorization and egress scope

The baseline Hermes profile is **planner-only**. It does not make Hermes the canonical executor or approval authority.

Concrete guarantees for the claimed profile are:

- `waiting_for_approval` immediately fails the planning boundary with canonical `FORBIDDEN`;
- the adapter does not call `POST /v1/runs/{run_id}/approval` to continue a waiting run;
- cancellation of a waiting run uses only the stop/reconcile path;
- recreating the adapter while a run is waiting still reconciles/cancels the same external run;
- a denied or absent canonical Approval therefore cannot be bypassed by this adapter, because no approval continuation bridge exists;
- Scenario B retains the reference executor as execution owner, so Hermes-native tool execution is not part of the compatibility claim.

For #591 specifically, there is no claimed Hermes-mediated canonical tool execution path in this profile. Canonical capability/egress enforcement remains on the platform-owned execution path, and the Hermes mapper fails closed for unmapped models/capabilities. A future profile that delegates executable tool calls to Hermes would require its own #591 egress regression before that capability could be claimed. The absence of such a profile is recorded as **not applicable**, not silently treated as a passing Hermes tool-egress test.

## #42 machine-readable adoption evidence

The original compatibility review is now represented through the existing #42 adoption contract instead of Markdown evidence alone:

- observation: `release/hermes-v0.21.1-observation.json`;
- revision-bound gate evidence: `release/hermes-v0.21.1-validation-evidence.json`;
- deterministic regression: `tests/release/test_hermes_v0_21_1_adoption_evidence.py`.

The regression reconstructs the pre-adoption inventory with the previously accepted Hermes revision and executes the existing read-only command:

```text
platform-release upstream-adoption-check \
  --inventory <pre-adoption compatibility snapshot> \
  --observations release/hermes-v0.21.1-observation.json \
  --component "Hermes Agent" \
  --evidence release/hermes-v0.21.1-validation-evidence.json \
  --compatibility-status tested \
  --reviewed-at 2026-09-11T00:00:00Z
```

The test requires the resulting compatibility inventory to move exactly from `63279301bcbdc185c1b07b98a9312eb0c862f26d` to `2237be355906fbe6065ce1815711eee52b2d646e`, while also proving that the adoption command remains read-only and does not rewrite its input inventory.

## Regression comparison against the previous pin

The adoption decision compares the old and candidate pins at the platform boundary rather than claiming whole-project behavioral equivalence across all 5,061 upstream commits.

- deterministic adapter contracts: candidate passes the same canonical #8 boundary plus v0.21.1-specific regressions;
- #19 evaluation/regression: passed;
- #46 lifecycle/E2E: passed against the exact candidate checkout;
- security policy: canonical approval/capability ownership is unchanged and fail-closed regressions pass;
- optional/absent Hermes: normal repository CI remains independent of an installed/running Hermes service;
- retry/error behavior: the canonical mapping contract is explicitly exercised for 4xx, 429, 5xx, timeout, cancellation, failure and unknown states;
- latency/resource consumption: no platform SLO or resource-envelope change is claimed by this pin update, and the adapter protocol/ownership model did not change. A synthetic cross-version microbenchmark would not establish production equivalence for an externally deployed planner, so no latency/resource pass is inferred. Any material deployment-specific regression remains an operational rollback trigger.

This adjudication makes the unmeasured metrics explicit instead of treating them as implicitly passed.

## Architecture invariants

No #733 change moves ownership into Hermes. In particular:

- canonical Task, Run, Event, Agent, Team, Plan and Step identities remain platform-owned;
- Hermes run/session IDs remain adapter-private;
- canonical authorization and approvals remain platform-owned;
- model routing and capability policy remain platform-owned;
- Hermes remains optional and separately deployed;
- the reference/non-Hermes path remains available;
- no Hermes-private retry loop is promoted into the canonical lifecycle.

No ADR is required because the architectural boundary from #8 is unchanged.

## Validation gates

The initial candidate decision passed on platform revision `256b5fc4380e81a0d6f7991ee9939fdc2df347a7`.

- **CI workflow:** run `34567628563` — success.
- **Core test job:** success, including Ruff format/lint, mypy, pytest, package build and MCP environment conformance.
- **#19 deterministic evaluation/regression gate:** success inside the core `test` job.
- **Hermes pinned compatibility job:** `hermes-pinned-compat` — success against exact Hermes commit `2237be355906fbe6065ce1815711eee52b2d646e`.
- **#46 Scenario B:** success inside `hermes-pinned-compat`, including version-specific regressions, real upstream lifecycle-surface validation, real `/v1/runs` adapter integration, exact Agent/Team mapping and Hermes + reference-executor kernel E2E.
- **Hermes conformance artifact:** `platform-conformance-hermes`, artifact id `10186986745`, digest `sha256:aa361f3f92a7dcffea99aece9a95248b485578fd014d14649a89d1e8a76590dd`.
- **Platform conformance workflow:** run `34567628589` — success.
- **Security/dependency gates:** CodeQL run `34567628481` and Dependency review run `34567628611` — success.
- **Final head of the original PR:** CI run `34569728229` also passed after the documentation evidence commit.

### Completion follow-up evidence

The missing evidence identified by the post-merge audit was completed on PR #767 at platform revision `2960cef6573a30bef2dd00b893968cec458ca22e`.

- **CI workflow:** run `34575804957` — success.
- **Core test job:** success, including Ruff format/lint, mypy, the full pytest suite, package build and MCP environment conformance.
- **#42 adoption regression:** `tests/release/test_hermes_v0_21_1_adoption_evidence.py` passed inside the full pytest suite and exercised the real read-only `upstream-adoption-check` path.
- **#19 deterministic evaluation/regression gate:** success inside the core `test` job.
- **Hermes pinned compatibility job:** `hermes-pinned-compat` — success after checking out exact Hermes commit `2237be355906fbe6065ce1815711eee52b2d646e`.
- **#46 Scenario B completion:** success, including explicit failed-state mapping, approval-wait fail-closed/cancellation/restart behavior, pinned-runtime adapter recreation, startup-key/authentication behavior and `/health` compatibility.
- **Hermes conformance artifact:** `platform-conformance-hermes`, artifact id `10189815331`, digest `sha256:7b9c6ce296793dc67e92f19432cae46b89e447521bc9acfe10cfc288970088ca`.
- **Reference execution independence:** `forge-sidecar-integration` also passed in CI run `34575804957`, confirming the completion work did not regress the independent Forge execution profile.
- **Security/dependency gates:** CodeQL run `34575804898` and Dependency review run `34575804897` — success.

The follow-up closes the previously missing evidence without changing the accepted Hermes revision. The final documentation-only PR head is revalidated before merge; a failing required check still blocks merge.

## Rollback

The rollback target remains the previously accepted commit `63279301bcbdc185c1b07b98a9312eb0c862f26d`. If a post-merge regression is discovered, revert the Hermes pin in the adapter, provenance, compatibility snapshots, example configuration and CI checkout together. No canonical Task/Run/Agent/Team/Plan/Step schema migration was introduced by this update, so rollback does not require a platform-domain data migration.

## Decision

**PASS_UPDATE**

Hermes Agent v0.21.1 at exact commit `2237be355906fbe6065ce1815711eee52b2d646e` satisfies the existing #8 adapter boundary and the #42 update process for the explicitly claimed planner-only compatibility surface. The pin update does not grant Hermes canonical lifecycle, approval, model-routing, capability, egress or execution ownership.

The governed Hermes pin remains `2237be355906fbe6065ce1815711eee52b2d646e`. The completion work closes the previously missing evidence rather than changing the accepted upstream revision.
