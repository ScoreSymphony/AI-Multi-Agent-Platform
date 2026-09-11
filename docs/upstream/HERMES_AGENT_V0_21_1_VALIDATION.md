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
| approval | `POST /v1/runs/{run_id}/approval` | not used as canonical approval authority |
| steer | `POST /v1/runs/{run_id}/steer` | not part of the baseline canonical contract |
| stop | `POST /v1/runs/{run_id}/stop` | cancellation/best-effort timeout cleanup |

The candidate idempotency implementation retains terminal states `completed`, `failed`, `cancelled` and `interrupted`. Hermes may also expose `waiting_for_approval`; the platform continues to fail closed rather than auto-approve it.

## Compatibility matrix

| Concern | Expected platform behavior | Evidence |
| --- | --- | --- |
| admission | task objective and planner instructions map to `/v1/runs` | real pinned-runtime integration test |
| polling | `queued` / `started` / `running` remain non-terminal | adapter regression + real pinned runtime |
| success | `completed` output normalizes to provider-neutral `PlanResponse` | regression + real pinned runtime |
| cancellation | `cancelled` / `interrupted` normalize to canonical `CANCELLED` | version regression |
| approval | `waiting_for_approval` normalizes to canonical `FORBIDDEN` | version regression |
| unknown state | unknown non-running state fails closed as `BACKEND_ERROR` | version regression |
| timeout | transport/provider deadline normalizes to canonical retryable `TIMEOUT` | version regression |
| HTTP/auth errors | 400/401/403/404/409/429/5xx retain canonical error classes | version regression |
| IDs | Hermes run IDs remain adapter-private metadata | real pinned-runtime integration + Scenario B |
| Agent/Team revisions | exact canonical revisions remain platform-owned | #8 mapper contract in Scenario B |
| model/tool bridge | explicit mapping remains fail-closed | #8 mapper contract + full CI |
| execution ownership | Hermes plans; reference executor remains canonical execution path | real pinned-runtime kernel E2E |
| disabled path | disabled adapter fails before network work | version regression + ordinary full CI |
| upstream route surface | run/status/events/approval/steer/stop routes exist at exact candidate checkout | real pinned-runtime lifecycle-surface test |

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

All required candidate gates passed on the tested platform revision `256b5fc4380e81a0d6f7991ee9939fdc2df347a7`.

- **CI workflow:** run `34567628563` — success.
- **Core test job:** success, including Ruff format/lint, mypy, pytest, package build and MCP environment conformance.
- **#19 deterministic evaluation/regression gate:** success inside the core `test` job.
- **Hermes pinned compatibility job:** `hermes-pinned-compat` — success against exact Hermes commit `2237be355906fbe6065ce1815711eee52b2d646e`.
- **#46 Scenario B:** success inside `hermes-pinned-compat`, including version-specific regressions, real upstream lifecycle-surface validation, real `/v1/runs` adapter integration, exact Agent/Team mapping and Hermes + reference-executor kernel E2E.
- **Hermes conformance artifact:** `platform-conformance-hermes`, artifact id `10186986745`, digest `sha256:aa361f3f92a7dcffea99aece9a95248b485578fd014d14649a89d1e8a76590dd`.
- **Platform conformance workflow:** run `34567628589` — success.
- **Security/dependency gates:** CodeQL run `34567628481` and Dependency review run `34567628611` — success.
- **Repository-wide operational checks:** all pull-request workflow runs associated with tested revision `256b5fc4380e81a0d6f7991ee9939fdc2df347a7` completed successfully, including performance, pressure, contention, fault, failover, reproducibility, regression, scale and prototype-acceptance workflows.

The evidence is revision-bound to the platform head above and the immutable Hermes candidate commit. No required matrix cell is being inferred from installation or process startup alone.

## Rollback

The rollback target remains the previously accepted commit `63279301bcbdc185c1b07b98a9312eb0c862f26d`. If a post-merge regression is discovered, revert the Hermes pin in the adapter, provenance, compatibility snapshots, example configuration and CI checkout together. No canonical Task/Run/Agent/Team/Plan/Step schema migration was introduced by this update, so rollback does not require a platform-domain data migration.

## Decision

**PASS_UPDATE**

Hermes Agent v0.21.1 at exact commit `2237be355906fbe6065ce1815711eee52b2d646e` satisfies the existing #8 adapter boundary and the required #42 update process for the claimed compatibility surface. The full repository CI, #19 deterministic evaluation gate, #46 Hermes Scenario B, exact pinned-runtime integration, lifecycle/status/error regressions, provenance consistency and security/dependency checks all passed on platform revision `256b5fc4380e81a0d6f7991ee9939fdc2df347a7`.

The governed Hermes pin may therefore be updated from `63279301bcbdc185c1b07b98a9312eb0c862f26d` to `2237be355906fbe6065ce1815711eee52b2d646e` without changing canonical lifecycle ownership or making Hermes mandatory.
