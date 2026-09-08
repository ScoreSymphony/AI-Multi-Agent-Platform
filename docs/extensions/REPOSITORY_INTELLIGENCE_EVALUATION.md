# Repository-intelligence provider evaluation

Issue #502 requires optional repository/code-intelligence providers to prove measurable value over
the deterministic platform baseline before adoption. This document defines the common evidence path.
It does not make any third-party provider part of the required runtime.

## Canonical evaluation boundary

`RepositoryIntelligenceEvaluationCaseExecutor` adapts the existing #502
`CapabilityToolProvider` boundary into the existing #19 `EvaluationRunner`. A versioned
`EvaluationCase` supplies only:

- the canonical repository-intelligence operation;
- provider-neutral operation arguments;
- ordinary #19 deterministic assertions and/or metric rules.

The executor records the provider output unchanged under `observation.data.output` and adds:

- canonical output-schema validity and bounded schema errors;
- provider ID, health and availability;
- exact source provenance validity for map/search/slice operations;
- immutable resolved-revision evidence;
- normalized freshness and whether the result is current enough to be source evidence;
- query latency in milliseconds;
- serialized result size in bytes;
- schema-error count.

This means the same suite can run once against the deterministic baseline and again against an
optional provider. #19 baseline comparison/regression policy remains the comparison authority; #502
does not introduce a second benchmark result store.

Source-derived provider output is considered current only for `live_revision`,
`workspace_snapshot`, `live_workspace` or `fresh_index`. `stale_index` and `unknown` remain valid
normalized states but are not accepted as current source evidence.

## Evidence still required for an adoption decision

The common executor covers contract correctness, provenance, freshness classification, latency and
response size. A real provider pilot must additionally collect, where applicable:

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

## ProjectAtlas first-pilot pin

The first candidate remains ProjectAtlas, not an adopted platform plugin.

The current stable upstream verification for the pilot is:

- upstream: `styler-ai/ProjectAtlas`;
- release: `v0.4.5`, published 2026-08-20 and marked non-prerelease;
- release target commit: `72b424b7bb79b0d413dfb8c1bdd8eae9dc4e196b`;
- license: MIT;
- Linux x86-64 release asset: `projectatlas-v0.4.5-x86_64-unknown-linux-gnu.tar.gz`;
- Linux x86-64 asset SHA-256: `e22ac7f9e37b1eb49929e4a8ad72c51affd2668731832652ee4783e20a8fabd9`.

The v0.4.5 CLI has an explicit top-level `--db` path and the source tree remains a separate project
selection. The upstream default is `.projectatlas/projectatlas.db`; therefore the platform pilot
must select a provider-owned database/cache path outside the canonical source checkout rather than
allowing the default project-local state path.

## ProjectAtlas pilot containment

Before any ProjectAtlas result is allowed to outrank the deterministic baseline, the executable
pilot must demonstrate all of the following:

1. the pinned binary checksum matches before execution;
2. the source tree is mounted/read as read-only input;
3. the selected `--db` path and any other provider state live in an isolated provider-owned writable
   directory;
4. no Git worktree creation/deletion, commit, push or canonical Workspace lifecycle operation is
   delegated to ProjectAtlas;
5. no platform secrets are provided;
6. egress is denied during indexed operation unless a later reviewed feature proves it is required;
7. CPU, RAM, process and disk budgets are bounded and measured;
8. clean-source and dirty-Workspace freshness are tested explicitly rather than inferred;
9. disable/removal leaves the repository and canonical Workspace unchanged and provider-owned derived
   state can be deleted/rebuilt deterministically;
10. the same #19 cases run against baseline and pilot and store comparable evidence.

Until these checks pass, ProjectAtlas remains a researched candidate only. Registry/Marketplace
metadata may describe the candidate or eventual plugin, but listing must not imply installation,
activation, trust, secret access or repository write authority.

## Next implementation step

The next #502 slice is the contained ProjectAtlas executable pilot plus the adapter that translates
its supported read-only surfaces into canonical repository-intelligence results. Only after that
pilot produces acceptable comparative evidence should a #20 capability-provider plugin manifest
and optional #81 Registry item be promoted as an installable integration.
