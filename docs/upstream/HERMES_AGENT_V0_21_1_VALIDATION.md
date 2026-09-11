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

The governed provenance entry is `upstream/hermes-agent.yaml`. The candidate pin is accepted on `main` only if the pull-request validation gates complete successfully.

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

The candidate pull request must pass all of the following before the updated pin is accepted:

1. full repository CI, including Ruff, mypy, pytest and build;
2. #19 deterministic evaluation/regression gate;
3. `hermes-pinned-compat` against exact commit `2237be355906fbe6065ce1815711eee52b2d646e`;
4. #46 Scenario B conformance, including the version-specific regression matrix, real upstream lifecycle-surface test, real `/v1/runs` adapter test, exact Agent/Team mapping test and Hermes + reference-executor kernel E2E;
5. repository pin/provenance/compatibility inventory consistency tests.

## Rollback

If any required validation gate exposes a regression, the update is not accepted. The rollback target is the previously accepted commit `63279301bcbdc185c1b07b98a9312eb0c862f26d`; revert the candidate pin in the adapter, provenance, compatibility snapshots, example configuration and CI checkout together.

## Decision

**PENDING_CI** on the candidate branch.

Static API, provenance and architecture review found no blocker that independently requires `DEFER` or `REJECT`. This is deliberately **not yet `PASS_UPDATE`**: #42 adoption policy requires revision-bound automated evidence, and the exact-candidate PR checks have not completed at the point this document is introduced. The document must be updated to one explicit final outcome — `PASS_UPDATE`, `DEFER` or `REJECT` — after those checks complete.
