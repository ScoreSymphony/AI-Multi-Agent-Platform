# Issue #502 completion record

Status: **final acceptance record for the provider-neutral v1 core; ProjectAtlas remains optional and experimental/deferred**.

This record supersedes the pre-consolidation status statements in
`ISSUE_502_INTEGRATION_HANDOFF.md`. That older document remains useful as historical integration
provenance, but its statements that formatter/lint/type/test/CI/CodeQL and ProjectAtlas containment
validation were still deferred no longer describe the current `main` line.

The architecture/scope decision is recorded explicitly in
[`ADR 0011`](../../adr/0011-repository-intelligence-v1-core-and-optional-providers.md). That ADR
supersedes the execution-status claims for #502 in the point-in-time 2026-09-07 roadmap snapshot.

## Final scope interpretation

The issue owner's scope correction makes repository/code intelligence itself part of operational
v1. The mandatory deliverable is the provider-neutral capability layer plus the deterministic local
baseline. Named third-party providers, production graph/semantic indexers and public Marketplace
publication remain optional extensions.

Accordingly, #502 can be complete without adopting ProjectAtlas, Graphify, CodeGraph or Understand
Anything as a default provider, provided the core remains fully usable without them and evaluated
candidate integrations remain safely optional.

## Core acceptance status

| Acceptance area | Final status | Evidence boundary |
| --- | --- | --- |
| deterministic local baseline remains usable | complete | `BaselineRepositoryIntelligenceProvider`, production single-node registration and baseline fixtures |
| Capability/Repository/Workspace integration | complete | canonical capability registry plus #82 Repository and #37 Workspace loaders |
| provider cannot own Repository/Workspace lifecycle | complete | read-only provider contracts and plugin permission boundaries |
| exact repository/workspace/revision/provider provenance | complete | Repository/Workspace-aware source snapshots and dirty-workspace evidence |
| unavailable/stale provider fallback | complete | `RepositoryIntelligenceFallbackInvoker` and completion fixtures |
| derived indexes remain non-canonical/rebuildable | complete | state classification plus ProjectAtlas external provider-state binding |
| authorized agent/planner/reviewer context funnel | complete | bounded search -> selected hit -> exact source slice -> Context candidate |
| scoped Search federation | complete | post-provider authorization and Workspace-scoped result projection |
| heavy provider work maps to scheduler requirements | complete as a contract | resource envelopes project to `JobRequirements`; unmeasured heavy work fails closed |
| realistic maintained provider pilot | complete | pinned ProjectAtlas v0.4.5 workflow with checksum verification |
| provider install/enable/disable/remove lifecycle | complete for candidate shell | #20-compatible ProjectAtlas candidate plugin tests |
| safe optional Registry representation | complete | technical catalog metadata and Registry validation fixtures |
| no paid provider/service required | complete | deterministic local baseline and local ProjectAtlas pilot path |

## Final ProjectAtlas evidence boundary

ProjectAtlas v0.4.5 is retained as an **evaluated optional candidate**, not a canonical architecture
choice.

The evaluation workflow proves, on Linux x86-64 and the exact pinned release asset:

- release SHA-256 verification before execution;
- source read-only during untrusted provider execution;
- provider database/state outside canonical source;
- no project-local `.projectatlas` state;
- stripped/minimal provider environment without platform secrets;
- `PR_SET_NO_NEW_PRIVS` plus seccomp denial of network socket/syscall paths;
- a pre-provider `AF_INET` self-test that must fail with `EPERM`;
- bounded JSON scan/search/slice/health/settings commands;
- source digest unchanged after the pilot;
- machine-readable timing/CPU/state observations plus a whole-pilot child-RSS high-water mark.

The final workflow additionally compares the candidate against a real deterministic Git reference
baseline on the **same immutable fixture revision**. It records like-for-like measured values for:

- cold time to useful context;
- warm query/slice time to useful context;
- tool calls to useful context;
- persistent provider-state bytes;
- baseline and candidate command timing/CPU observations.

Provider-specific raw stdout byte counts are retained only as transport observations. They are not
compared as model-context size because the Git and ProjectAtlas outputs use different envelopes and
have not been normalized to the same logical payload. Comparable peak RSS is likewise left
unmeasured: `RUSAGE_CHILDREN.ru_maxrss` is a process-lifetime child high-water mark, so the final
artifact records it only at the whole-pilot level rather than attributing it to individual commands.

The comparison deliberately leaves the following unmeasured rather than inventing values:
normalized model-context bytes/tokens, comparable baseline-vs-candidate peak RSS, representative
agent first-pass success, symbol/reference/dependency correctness, architecture/domain/impact
usefulness, large-repository incremental/rebuild cost and dirty-workspace freshness for ProjectAtlas
itself.

## ProjectAtlas decision

Final #502 disposition: **experimental/deferred**.

That status means:

- ProjectAtlas is not the default provider;
- production source/search/slice/symbol/graph capabilities are not activated through the candidate
  plugin shell;
- the plugin shell may expose only the already-reviewed health/index-status surface;
- the deterministic platform baseline remains the portability floor and ordinary runtime path;
- the successful evaluation-only network sandbox must not be misrepresented as a generic production
  worker sandbox.

A future issue may reconsider ProjectAtlas source capabilities. Such work must first add
version-pinned raw golden outputs/strict normalizers, representative correctness and resource
benchmarks, dirty-workspace provider freshness evidence, and an explicitly deployment-owned
production process boundary. None of that is required to make the provider-neutral #502 v1 core
complete.

## Why the candidate is not adopted now

The tiny deterministic fixture supplies real comparison evidence but does not establish enough
unique value to justify making an additional persistent indexer part of the default operational
surface. In particular, it does not measure the graph/symbol/impact features that could distinguish
ProjectAtlas from the baseline, and it does not establish representative large-repository rebuild
or dirty-workspace behavior.

The safe decision is therefore to retain the integration seam and evaluated candidate while keeping
source capabilities disabled. This preserves reversibility and avoids turning optional provider
work into a v1 blocker.

## Final validation gate

Historical note: the dedicated `repository-intelligence-projectatlas-pilot.yml` workflow was the
closing validation gate for #502. After #502 was completed and ProjectAtlas remained
experimental/deferred, that dedicated active CI workflow was retired during workflow consolidation;
the scripts, documentation and recorded evaluation evidence remain as provenance.

The closing PR for #502 was required to satisfy all normal protected-branch requirements on its
exact head and also pass the ProjectAtlas comparison workflow, which emitted the machine-readable
contained-pilot plus baseline-comparison artifact.

The closing check required confirmation that:

1. the branch was not behind current `main`;
2. no merge conflicts existed;
3. all required protected-branch checks were successful;
4. the ProjectAtlas comparison workflow was successful on the exact head;
5. no unresolved P1/P2 review blocker remained.

Once those conditions held, #502 was complete at its clarified v1 boundary. Further third-party
provider adoption belongs in follow-up work rather than keeping the core issue open indefinitely.
