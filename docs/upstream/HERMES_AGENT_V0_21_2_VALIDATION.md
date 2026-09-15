# Hermes Agent v0.21.2 validation

Issue: #959

## Status

Validation is complete. The final adoption decision is `PASS_UPDATE`. Hermes Agent
v0.21.2 at exact commit `939e45c91d751fadd94dcd1b873ac3cb44846213` is the accepted production/default revision. The
previous v0.21.1 revision remains retained as the rollback baseline.

## Retained rollback baseline

- release: Hermes Agent v0.21.1
- tag: `v2026.9.7`
- exact rollback commit: `2237be355906fbe6065ce1815711eee52b2d646e`
- retained evidence: `docs/upstream/HERMES_AGENT_V0_21_1_VALIDATION.md`
- rollback target after v0.21.2 adoption: the exact v0.21.1 commit above

## Candidate provenance

- canonical upstream: `https://github.com/NousResearch/hermes-agent`
- release: Hermes Agent v0.21.2
- release tag: `v2026.9.11`
- annotated tag object: `2160b2d59c87316e82f749d77c1f25969bea1533`
- exact tag target commit: `939e45c91d751fadd94dcd1b873ac3cb44846213`
- tagger timestamp: `2026-09-11T19:20:28Z`
- GitHub release publication: `2026-09-11T19:20:31Z`
- license: MIT (unchanged from the accepted upstream inventory)
- annotated tag signature: unsigned; provenance therefore relies on the canonical
  upstream repository, exact annotated-tag object and exact resolved commit rather
  than a verified tag signature

The upstream release notes contain measurements taken at an earlier release-preparation
commit. This validation deliberately uses the annotated tag's resolved commit
`939e45c91d751fadd94dcd1b873ac3cb44846213` as the candidate authority.

## Diff classification

GitHub's exact comparison from the accepted v0.21.1 commit to the v0.21.2 tag target is:

- status: ahead
- merge base: `2237be355906fbe6065ce1815711eee52b2d646e`
- ahead: 986 commits
- behind: 0 commits

Despite the patch-level version number this is therefore treated as a substantive
upstream review. The release is especially relevant to #959 because upstream changed
state/session persistence, WAL/read behavior, writer ownership and profile/session
isolation in addition to many unrelated surfaces.

## Release-specific upstream fixes under validation

The revision-bound candidate gate runs the exact source checkout and includes upstream
regressions covering:

- malformed/corrupt session-row degradation rather than whole-list failure;
- read-path transient SQLite IOERR handling;
- active WAL confirmation and deleted-WAL generation safety;
- shared SessionDB registry/lifecycle behavior;
- session resume database ownership and launch-home/profile isolation;
- settled SessionDB opens performing no unnecessary main-database writes;
- opening/reading while another connection holds the write transaction;
- durable `/v1/runs` recovery where a dead pre-restart owner becomes explicit
  `interrupted` state.

Relevant upstream release PRs reviewed for the targeted matrix include #108067,
#108074, #108076, #108082, #108086 and #108130 in `NousResearch/hermes-agent`.

## Platform candidate gate

The pre-adoption gate was intentionally separate from `hermes-pinned-compat`:

- workflow: `.github/workflows/hermes-v0-21-2-candidate.yml`
- runner: `scripts/ci/hermes_v0_21_2_candidate_validation.py`
- platform integration test:
  `tests/integration/upstreams/test_hermes_v0_21_2_candidate.py`

This separation enforced the #959 adoption rule: candidate validation could not silently
turn an unverified revision into `HERMES_PINNED_REVISION` merely to make the compatibility
job pass.

The revision-bound pre-adoption run recorded below established, before promotion, that:

- the repository's accepted production pin remained the exact v0.21.1 baseline while
  v0.21.2 was evaluated separately;
- the candidate was exercised explicitly as `UNVERIFIED_PIN` before the adoption
  decision;
- profile selection remained namespaced in the Hermes URL boundary;
- two real `/v1/runs` executed concurrently through the candidate API server using a
  deterministic barrier, returned distinct external run IDs, preserved their canonical
  task IDs and did not cross-contaminate planner output;
- cancelled/interrupted/waiting-for-approval/unknown statuses remained fail-closed under
  canonical platform error mapping.

After `PASS_UPDATE`, the same v0.21.2 regression matrix is retained in the repository and
runs against the now-verified accepted pin.

## Boundary assessment

No v0.21.2 release item changes the platform ownership model by itself. The validation
continues to require:

- Task/Run/Agent/Team/Plan/Step identity and lifecycle ownership in the platform;
- Hermes run/session/profile IDs only as namespaced adapter metadata;
- platform-owned authorization and approval authority;
- execution remaining independent of Hermes;
- Hermes remaining optional and replaceable.

The platform adapter supports an explicit Hermes `profile` prefix. The candidate gate
therefore combines platform URL-namespace coverage with upstream's revision-bound
profile/session database-ownership tests; it does not promote any Hermes profile state
into canonical platform state.

## Decision

Final result: **`PASS_UPDATE`**.

Revision-bound pre-adoption evidence for `939e45c91d751fadd94dcd1b873ac3cb44846213`:

- dedicated Hermes v0.21.2 candidate workflow `34759634674`: passed, including the
  exact-upstream malformed-row, WAL/IOERR, competing-writer, reader/writer,
  SessionDB/profile ownership and restart/dead-owner reliability matrix;
- #46 Hermes Scenario B in the same candidate workflow: passed against the exact
  candidate checkout and the non-Hermes reference executor path;
- repository CI workflow `34759634728`: passed, including the full Pytest suite,
  deterministic #19 evaluation/regression gate, static checks and package build;
- CodeQL workflow `34759634698`: passed; repository-governance and test-layout
  workflows for the same head also passed;
- canonical ownership remained unchanged: Hermes-native run/session/profile IDs stay
  namespaced adapter metadata, while Task/Run/Agent/Team/Plan/Step, authorization and
  approvals remain platform-owned.

The adapter's explicit profile URL namespace plus the upstream revision-bound
SessionDB ownership, canonical-profile listing and launch-home isolation regressions
cover the profile/session isolation claimed by the adapter. No broader multi-profile
runtime authority is claimed.

The accepted pin may therefore move to v0.21.2. The rollback target remains Hermes
Agent v0.21.1 / tag `v2026.9.7` / `2237be355906fbe6065ce1815711eee52b2d646e` with its original validation and evidence
artifacts retained unchanged.

Promotion updates the governed pin, compatibility metadata, standard pinned Hermes
conformance profile and integration documentation with this decision.
