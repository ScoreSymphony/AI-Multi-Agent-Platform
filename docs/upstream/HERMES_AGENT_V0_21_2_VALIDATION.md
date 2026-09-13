# Hermes Agent v0.21.2 validation

Issue: #959

## Status

Validation is in progress. The accepted production/default Hermes revision remains
v0.21.1 until every required compatibility, reliability, regression and conformance
gate has passed. No adoption decision has been issued yet.

## Accepted baseline

- release: Hermes Agent v0.21.1
- tag: `v2026.9.7`
- exact accepted commit: `2237be355906fbe6065ce1815711eee52b2d646e`
- retained evidence: `docs/upstream/HERMES_AGENT_V0_21_1_VALIDATION.md`
- rollback target if v0.21.2 is later adopted: the exact v0.21.1 commit above

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

The pre-adoption gate is intentionally separate from `hermes-pinned-compat`:

- workflow: `.github/workflows/hermes-v0-21-2-candidate.yml`
- runner: `scripts/ci/issue959_hermes_v0_21_2_candidate.py`
- platform integration test:
  `tests/integration/upstreams/test_hermes_v0_21_2_candidate.py`

This separation enforces the #959 adoption rule: candidate validation cannot silently
turn an unverified revision into `HERMES_PINNED_REVISION` just to make the compatibility
job pass.

The platform-side candidate tests currently prove:

- the repository's accepted pin remains the exact v0.21.1 baseline during validation;
- the candidate is configured explicitly as `UNVERIFIED_PIN`;
- profile selection remains namespaced in the Hermes URL boundary;
- two real `/v1/runs` execute concurrently through the candidate API server using a
  deterministic barrier, return distinct external run IDs, preserve their canonical
  task IDs and do not cross-contaminate planner output;
- cancelled/interrupted/waiting-for-approval/unknown statuses remain fail-closed under
  canonical platform error mapping.

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

## Remaining gates before adoption

The following evidence must still be green before `PASS_UPDATE` can be issued:

1. the dedicated v0.21.2 candidate workflow;
2. standard adapter/unit/static/package tests on the candidate branch;
3. #19 deterministic evaluation/regression gate;
4. #46 Hermes Scenario B against the exact candidate revision;
5. required repository CI and security/dependency checks;
6. final review of the candidate results and an explicit `PASS_UPDATE`, `DEFER` or
   `REJECT` decision.

Only after `PASS_UPDATE` may the canonical Hermes inventory, adapter pin, compatibility
inventories and standard pinned-conformance workflow move from v0.21.1 to v0.21.2.
