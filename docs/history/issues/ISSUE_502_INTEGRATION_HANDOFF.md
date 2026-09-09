# Issue #502 unified-integration handoff

Status: **prepared consolidation input; do not merge independently**.

This document is the handoff for the later branch that will combine all active AI-Multi-Agent-Platform work. It intentionally does not claim that the prepared code has passed branch-local CI, type checking, security gates, or the ProjectAtlas containment/evaluation campaign.

## Current #502 branch stack

The #502 preparation is intentionally linear:

1. `issue-502-projectatlas-plugin-shell`
2. `issue-502-projectatlas-egress-pilot`
3. `issue-502-completion-prep`

`issue-502-completion-prep` already contains the history of the two preceding branches. The aggregate branch should therefore normally integrate **only the final completion branch**, not cherry-pick all three independently and duplicate commits.

Existing PRs #653 and #655 remain useful provenance/review records for the intermediate plugin and containment slices. They are not intended to be merged separately once the aggregate branch is assembled.

## Foundation already on main

The consolidated `main` produced by PR #645 already contains the provider-neutral #502 foundation:

- deterministic repository-intelligence baseline;
- canonical capability taxonomy;
- Repository/Workspace-aware provenance and dirty-workspace freshness support;
- #19 provider evaluation executor;
- pinned ProjectAtlas v0.4.5 functional pilot harness and evidence;
- technical Registry/Marketplace candidate catalog foundation.

The completion branch adds only the remaining candidate/plugin/consumer/resource/fallback preparation on top of that foundation.

## Prepared completion surfaces

### ProjectAtlas candidate plugin

The #20-compatible candidate plugin:

- pins ProjectAtlas v0.4.5 and the evaluated Linux x86-64 archive checksum;
- requests capability-registration and worker-execution permissions only;
- requests no repository-write, Workspace-lifecycle, secret, or network permission;
- registers only `repository.health` and `repository.index_status` while source operations are gated;
- keeps provider-owned derived state non-canonical and rebuildable;
- can be enabled, disabled and removed through the existing Plugin lifecycle.

### Process containment pilot

The evaluation-only Linux x86-64 wrapper prepares:

- `PR_SET_NO_NEW_PRIVS`;
- seccomp network-syscall denial;
- x32 ABI rejection;
- unrelated file-descriptor closure;
- a real `AF_INET socket()` self-test before the untrusted runtime;
- execution of the ProjectAtlas version probe and pilot commands behind the same filter.

This is **not** a generic production sandbox. The aggregate campaign must decide whether this narrow boundary is sufficient evidence for the ProjectAtlas pilot and what deployment-owned worker/process boundary is required before any production source capability is activated.

### Canonical runtime fallback

`RepositoryIntelligenceFallbackInvoker` handles provider degradation detected only after a repository-specific invocation has begun.

Fallback policy:

- preferred provider is invoked through the ordinary #12 `CapabilityInvoker` path;
- `UNAVAILABLE` and `TIMEOUT` can fall back to a baseline-only canonical invoker for baseline-equivalent capabilities;
- provider-specific requirements fail closed and do not silently degrade;
- both attempts retain the ordinary policy/authorization/audit/tool-invocation boundary.

The aggregate composition must construct the baseline-only invoker explicitly; it must not create a hidden direct provider call that bypasses #12.

### Agent / Planner / Reviewer / Research context funnel

`RepositoryContextFunnel` prepares the optional #502 context flow:

`query -> bounded repository search -> selected paths -> exact bounded source slices -> #590 ContextCandidate`

Properties:

- no broad repository read is required by the funnel itself;
- source slices retain repository/revision/provider/workspace provenance;
- provider summaries are never promoted to instruction/security authority;
- repository paths are not misrepresented as canonical #30 File IDs;
- the #590 resolver remains responsible for scope, authorization, conflict, freshness and budget handling;
- the same adapter can be composed for Developer Agents, Planner, Reviewer/Verification and Research consumers.

### Scoped #45 Search federation

`RepositoryIntelligenceSearchFederator` exposes provider-backed text hits on demand without copying private Workspace source into a second global index.

The aggregate Search composition must preserve:

- Project/Workspace scope checks before invocation;
- ordinary capability authorization on the repository-intelligence invocation;
- post-provider result authorization before count/result disclosure;
- provider and immutable source revision in result provenance;
- distinction between canonical #45 Search state and provider-private derived indexes.

### Resource / scheduler integration

Repository-intelligence workloads are classified as:

- `bounded_query`;
- `incremental_refresh`;
- `full_rebuild`.

Resource envelopes project into the existing #14 `JobRequirements` contract. Heavy work defaults to fail-closed when CPU/RAM/storage bounds are unknown.

The existing ProjectAtlas tiny-fixture evidence is recorded as observation only:

- peak child RSS: 19,032 KiB;
- provider state: 820,744 bytes.

These values are **not** production admission minima. The aggregate performance campaign must measure representative repository sizes before supplying `incremental_refresh` or `full_rebuild` admission bounds.

### Evaluation matrix

The prepared comparison model records the full #502 metric vocabulary without converting unmeasured values to zero. Baseline and candidate observations are comparable only when fixture, immutable source revision and environment reference match.

The later campaign should populate, where applicable:

- time to useful context;
- Task/first-pass success;
- tool calls, broad reads and backtracking;
- model-context bytes/tokens;
- provenance and source-slice accuracy;
- symbol/reference/dependency correctness;
- architecture/domain/impact usefulness;
- dirty-workspace freshness;
- index/update/rebuild/query/startup latency;
- CPU/RAM/state growth;
- repair/rebuild burden and recovery;
- network, secret, external-service and paid-service requirements.

No marketing token-savings claim is evidence unless reproduced through this platform-specific comparison path.

## Prepared acceptance coverage

`tests/test_issue_502_completion_prep.py` is intentionally prepared but not executed on this branch. It covers the remaining integration contracts:

| #502 requirement | Prepared evidence |
| --- | --- |
| provider unavailable/stale fallback | runtime fallback fixture with provider-specific fail-closed case |
| minimal context funnel | bounded search -> one exact source slice, zero broad full-file reads |
| revision/provider/workspace provenance | context and Search federation fixtures |
| dirty Workspace freshness | live-workspace source binding and Context freshness fixture |
| scoped Search | post-provider authorization and Workspace-scoped result projection |
| provider state outside source | `ProjectAtlasSourceBinding` path invariant |
| source capability containment gate | incomplete containment fails `UNAVAILABLE`; complete evidence becomes eligible |
| heavy rebuild resource admission | incomplete envelope fails closed; measured deployment bounds project to #14 |
| provider-vs-baseline comparison | comparable observations, deltas, explicit missing metrics |

The earlier #502 tests on `main` continue to cover baseline repository navigation, production Repository authorization, tree materialization bounds, dirty-workspace snapshot behavior, provider evaluation schema/provenance and the initial ProjectAtlas pilot harness.

## ProjectAtlas v0.4.5 golden-output capture

Do **not** implement a guessed `ProjectAtlasV045Normalizer` before the aggregate campaign.

During unified validation:

1. download the exact pinned v0.4.5 Linux x86-64 artifact;
2. verify the recorded SHA-256 before execution;
3. run only behind the selected containment boundary;
4. execute the deterministic fixture commands already used by the pilot;
5. persist the exact raw JSON stdout for `scan`, `search`, `slice`, `health-check` and `settings` under a fixture directory dedicated to v0.4.5;
6. review the payload for volatile/provider-private identifiers;
7. write a strict version-pinned normalizer from those captured payloads into canonical #502 schemas;
8. add negative fixtures for missing/renamed/wrong-typed fields;
9. only then allow the ProjectAtlas source provider to register baseline-equivalent source capabilities.

A newer upstream ProjectAtlas release requires a new golden-output/version evaluation. Do not silently reuse the v0.4.5 normalizer for a different runtime version.

## Aggregate composition order

Recommended order inside the future all-active-branches integration branch:

1. start from the then-current `main`;
2. integrate the current #591 egress/security hardening before final ProjectAtlas production composition;
3. integrate `issue-502-completion-prep` once, because it already contains the #653/#655 history;
4. reconcile shared exports/imports after all active branches are present;
5. compose repository-intelligence consumers into the final single-node/deployment runtime only after concurrent deployment changes from other active issues have been combined;
6. capture ProjectAtlas v0.4.5 golden outputs and implement the strict normalizer if containment remains acceptable;
7. measure representative resource and provider-vs-baseline fixtures;
8. make the explicit candidate decision;
9. run the unified validation gates once on the integrated tree.

## Known conflict hotspots

The completion branch was designed to minimize edits to shared composition points. During consolidation, pay particular attention to:

- `src/ai_multi_agent_platform/repository_intelligence/__init__.py` — preserve exports from every #502 slice;
- `src/ai_multi_agent_platform/deployment/single_node.py` — final consumer/provider composition should be done only after other active deployment branches are combined;
- capability/egress composition — keep #591 policy and #502 OS-process containment as distinct layers;
- Search composition — preserve other #45 resource providers and add repository federation without replacing canonical Search;
- #590 Context source composition — add repository intelligence as a source adapter, never as context authority;
- #14/#500 scheduling — project heavy provider work into the existing scheduling/pressure model rather than introducing another queue.

## Candidate decision gate

ProjectAtlas remains `experimental/candidate` until the unified campaign supplies the missing real evidence.

### Eligible for baseline-equivalent source capabilities

Only if all are true:

- exact runtime/checksum match;
- Repository/Workspace authorization occurs before source access;
- source is read-only to the untrusted provider;
- provider state is outside canonical source;
- secrets are stripped;
- network egress is actually denied by the selected execution boundary;
- `no_new_privileges`/equivalent process hardening is active where applicable;
- dirty Workspace/revision provenance is correct;
- strict v0.4.5 output normalizers pass captured golden/negative fixtures;
- resource admission exists for heavy work;
- deterministic baseline fallback works;
- representative comparison demonstrates enough benefit to justify maintenance/security/resource cost.

### Possible final outcomes

- `adopted`: justified general provider and packaged/activated by policy;
- `specialist/on-demand`: unique graph/impact value but not default;
- `experimental`: useful but evidence/maintenance/security is not mature enough;
- `reference-only`: catalog/documentation value, no runtime integration;
- `rejected`: cost/security/maintenance outweighs measured benefit;
- `deferred`: insufficient representative evidence.

A decision to reject ProjectAtlas does **not** invalidate #502. The deterministic baseline and provider-neutral seams remain the canonical deliverable.

## Validation explicitly deferred

Do not treat this preparation branch as validation evidence. The following are intentionally deferred to the later all-active-branches integration branch:

- formatter/lint/type checking;
- unit/integration/end-to-end tests;
- CI and Required Checks;
- CodeQL/dependency/security workflows;
- Linux seccomp real execution evidence;
- ProjectAtlas golden-output capture;
- representative large-repository CPU/RAM/disk measurements;
- provider-vs-baseline agent/planner/reviewer comparison;
- final ProjectAtlas adoption status.

Issue #502 should remain open until that unified campaign has reconciled the active branches and the resulting integrated tree satisfies the acceptance criteria.
