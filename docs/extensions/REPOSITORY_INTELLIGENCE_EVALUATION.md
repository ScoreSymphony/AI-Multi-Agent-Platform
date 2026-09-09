# Repository-intelligence provider evaluation

Issue #502 requires optional repository/code-intelligence providers to prove measurable value over
the deterministic platform baseline before adoption. This document defines the common evidence path.
It does not make any third-party provider part of the required runtime.

## Canonical evaluation boundary

`RepositoryIntelligenceEvaluationCaseExecutor` adapts the #502 `CapabilityToolProvider` boundary into
the existing #19 `EvaluationRunner`. A versioned `EvaluationCase` supplies only:

- the canonical repository-intelligence operation;
- provider-neutral operation arguments;
- ordinary #19 deterministic assertions and/or metric rules.

The executor records provider output unchanged under `observation.data.output` and adds:

- canonical output-schema validity and bounded schema errors;
- provider ID, health and availability;
- exact source provenance validity for map/search/slice operations;
- immutable resolved-revision evidence;
- normalized freshness and whether the result is current enough to be source evidence;
- query latency in milliseconds;
- serialized result size in bytes;
- schema-error count.

The same suite can therefore run against the deterministic baseline and an optional provider once
that provider exposes normalized canonical source capabilities. #19 baseline comparison/regression
policy remains the comparison authority; #502 does not introduce a second durable benchmark store.

Source-derived provider output is considered current only for `live_revision`,
`workspace_snapshot`, `live_workspace` or `fresh_index`. `stale_index` and `unknown` remain valid
normalized states but are not accepted as current source evidence.

## Evidence required before provider adoption

The common executor covers contract correctness, provenance, freshness classification, latency and
response size. A provider considered for production source capabilities must additionally collect,
where applicable:

- initial index/build duration and incremental refresh duration;
- CPU, peak RAM, disk/index size and index growth;
- source/full-file reads, repeated exploration and model-context bytes/tokens;
- symbol/reference/dependency/impact correctness for enhanced capabilities;
- clean revision, immutable Workspace Snapshot and dirty live-Workspace behavior;
- disable, repair, rebuild and removal behavior;
- filesystem writes and exact provider-owned state location;
- network attempts, required egress, secrets and external-service dependencies.

Marketing token-savings claims are never copied into platform evidence. Only reproduced measurements
from the common workload count.

## ProjectAtlas v0.4.5 evaluation pin

The first evaluated candidate is ProjectAtlas, not an adopted default provider.

The pinned upstream evidence is:

- upstream: `styler-ai/ProjectAtlas`;
- release: `v0.4.5`, published 2026-08-20 and marked non-prerelease;
- release target commit: `72b424b7bb79b0d413dfb8c1bdd8eae9dc4e196b`;
- license: MIT;
- Linux x86-64 release asset: `projectatlas-v0.4.5-x86_64-unknown-linux-gnu.tar.gz`;
- Linux x86-64 asset SHA-256: `e22ac7f9e37b1eb49929e4a8ad72c51affd2668731832652ee4783e20a8fabd9`.

The v0.4.5 CLI has an explicit top-level `--db` path and the source tree remains a separate project
selection. The platform pilot therefore selects a provider-owned database/cache path outside the
canonical source checkout rather than allowing the default `.projectatlas/projectatlas.db` state.

## ProjectAtlas pilot containment status

The current Linux x86-64 evaluation workflow verifies:

1. the pinned binary checksum before execution;
2. read-only canonical source during untrusted provider execution;
3. provider state in an isolated writable directory outside source;
4. no delegated Git/Workspace lifecycle ownership;
5. no platform secrets in the provider environment;
6. `PR_SET_NO_NEW_PRIVS` plus seccomp denial of socket/network syscalls;
7. a pre-provider `AF_INET` self-test that must fail with `EPERM`;
8. source-tree digest unchanged after the pilot;
9. no project-local `.projectatlas` state;
10. measured command latency/CPU and provider-state size on the deterministic tiny fixture, plus a
    whole-pilot child-RSS high-water mark.

`RUSAGE_CHILDREN.ru_maxrss` is a process-lifetime maximum across terminated children. The final
comparison artifact therefore does not attribute that value to individual commands and does not
compare it with baseline RSS.

This proves **evaluation containment**, not a generic production sandbox. The candidate plugin shell
does not itself install that process boundary around future source operations, so production source
capabilities remain disabled.

## Real baseline-vs-candidate comparison

The final #502 workflow runs `scripts/ci/issue502_projectatlas_comparison.py`. The wrapper forces a
deterministic Git commit timestamp so the ProjectAtlas pilot and the local Git reference baseline
operate on byte-identical fixtures with the same immutable commit revision. The workflow fails if
the revisions differ.

The local reference path measures bounded repository navigation/search/slice work with Git and owns
no persistent index. The ProjectAtlas path measures the pinned candidate on the same source.

The resulting machine-readable artifact records like-for-like comparison metrics for:

- cold time to useful context: baseline `search + slice`, candidate `scan + search + slice`;
- warm time to useful context: baseline `search + slice`, candidate `search + slice`;
- tool calls to useful context;
- persistent provider-state bytes;
- per-command elapsed time and CPU observations;
- exact fixture revision and correctness checks for the expected search hit/source slice.

Raw stdout byte counts are retained separately as **provider-specific transport observations**. They
are not compared as model-context size because the Git baseline and ProjectAtlas return different
envelopes. A future normalized canonical-output evaluation may compare model-context bytes/tokens
only after both paths are reduced to the same logical payload.

The comparison intentionally records the following as unmeasured/not comparable instead of zero:

- normalized model-context bytes/tokens;
- comparable baseline-vs-candidate peak RSS;
- representative agent first-pass success;
- symbol/reference/dependency correctness;
- architecture/domain/impact usefulness;
- large-repository incremental refresh/rebuild cost;
- dirty-Workspace freshness for ProjectAtlas itself.

That distinction matters: the tiny fixture supplies real baseline-vs-candidate evidence for a
conservative decision, but it cannot justify claims about ProjectAtlas's differentiated graph or
semantic value.

## Final #502 provider decision

ProjectAtlas is **experimental/deferred** for the #502 completion boundary.

The current evidence is sufficient to:

- retain the pinned candidate and its #20-compatible health/index-status plugin shell;
- demonstrate reversible install/enable/disable/remove behavior;
- demonstrate evaluation-only read-only/no-network containment;
- demonstrate a real measured comparison against the deterministic local baseline;
- represent the candidate safely in the optional technical Registry/catalog.

The current evidence is **not** sufficient to:

- make ProjectAtlas the default repository-intelligence provider;
- enable production repository map/search/source-slice/symbol/graph capabilities;
- treat the CI seccomp wrapper as a deployment-owned production sandbox.

A future adoption issue must add strict version-pinned raw-output normalization, representative
correctness/resource/freshness evidence and a production process boundary before source capabilities
can outrank the baseline.

This deferred candidate decision does not block #502 itself. The clarified v1 requirement is the
provider-neutral repository/code-intelligence core and deterministic local baseline, both of which
remain usable without any third-party provider. The architecture decision is recorded in
`docs/adr/0011-repository-intelligence-v1-core-and-optional-providers.md`; see
`docs/history/issues/ISSUE_502_COMPLETION.md` for the final acceptance record.
