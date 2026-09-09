# Repository / Code Intelligence

Issue #502 makes repository/code intelligence part of the **required operational v1 core**. The
required core is provider-neutral and ships with a deterministic local baseline. Optional
third-party indexers/providers may improve navigation, symbol/graph/semantic analysis or latency,
but they are enhancements rather than prerequisites for a usable platform.

Repository intelligence does not create a second Repository, Git, Workspace, Search, Task, Agent
or authorization authority.

## Ownership boundaries

The following boundaries remain authoritative:

- repository identity, Git operations and collaboration resources: #82 Repository integration;
- Workspace/materialization lifecycle and leases: #37 Workspaces;
- capability discovery/invocation: #12 Capability Registry;
- extension lifecycle: #20 Plugins;
- authorization/Approvals: #15;
- optional distribution/catalog metadata: #81 Registry/Marketplace.

Repository-intelligence providers may read only the repository/Workspace scope granted to the
calling operation. They must not create/delete worktrees, commit, push, acquire Workspace
ownership, expand authorization scope or treat derived indexes as project truth.

## Canonical capability taxonomy

The first version defines these read-only capabilities:

| Capability | Purpose |
| --- | --- |
| `repository.map` | bounded file map at an exact source view |
| `repository.text_search` | deterministic baseline text search |
| `repository.source_slice` | exact bounded source lines |
| `repository.health` | provider health |
| `repository.index_status` | freshness/index state |

Future plugins may add compatible symbol/reference/dependency/impact/semantic/domain capabilities.
Candidate-specific types must remain behind the capability/plugin boundary.

Every source-derived result includes:

- canonical repository ID;
- requested revision;
- immutable resolved Git revision used as base provenance;
- intelligence provider ID;
- normalized freshness evidence.

Run-bound Workspace results additionally identify the canonical Workspace/Snapshot and, when a
live materialization is used, its materialization ID, source-content checksum and explicit dirty
state. A dirty Workspace never receives a fabricated Git revision: the immutable Git commit stays
base provenance while `freshness=live_workspace` and `dirty=true` identify the actual source view.

Provider summaries never outrank current source, tests or canonical repository state.

## Required deterministic baseline

`BaselineRepositoryIntelligenceProvider` is deliberately small and dependency-free. It consumes an
injected exact `RepositoryTree` snapshot loader, performs deterministic bounded map/text/slice
operations in-process, and owns no persistent index.

Production single-node composition supplies its immutable Repository path through
`AuthorizedRepositorySnapshotLoader`, which reads via the canonical #82 `RepositoryService` and its
#15 authorization boundary. The provider itself never receives a provider-private Git path or
worktree.

During a canonical Run, `WorkspaceAwareRepositoryIntelligenceProvider` first asks
`AuthorizedRunWorkspaceSnapshotLoader` whether the requested repository/revision is the repository
source bound by the Run's immutable `RunWorkspaceBinding`:

1. #15 authorizes `read` on the exact Workspace before bytes are read;
2. the Run binding must agree with Workspace ID, Snapshot ID, Task ID and Snapshot checksum;
3. an inactive bound Run reads the immutable #37 Workspace Snapshot via canonical `FileProvider`;
4. an active local Run reads its bounded materialization through the single-node composition seam,
   rejects symlinks and applies file-count/byte ceilings;
5. dirty content is marked `freshness=live_workspace` with a content checksum while preserving the
   immutable base Git revision;
6. if an active materialization exists but cannot be observed safely, the loader fails closed
   rather than silently returning the stale input Snapshot;
7. if the caller explicitly requests another revision, the ordinary #82 repository path resolves
   that revision instead of substituting the Run Workspace.

The reference provider is the portability floor. Local deployments may later supply optimized
Git/ripgrep/LSP-backed implementations, but those tools remain implementation choices rather than
new canonical data models. LSP/symbol semantics are not fabricated when no LSP is configured.

## Freshness classes

Source/index results use normalized freshness values:

- `live_revision`: exact immutable Repository revision read through #82;
- `workspace_snapshot`: exact immutable #37 Snapshot bound to a Run;
- `live_workspace`: current bounded materialization content for an active Run, with explicit dirty
  evidence;
- `fresh_index`: provider-owned derived index proven current for the requested source view;
- `stale_index`: derived index known not to represent the requested source view;
- `unknown`: freshness cannot currently be established.

A stale or unknown optional index must never be presented as current source truth. Providers whose
source-derived answers depend on a stale index must become unavailable for those capabilities so
the deterministic baseline can win resolution.

## Provider selection and fallback

All implementations publish the same canonical `CapabilitySpec` for a capability/version.
`CapabilityRegistry` therefore owns selection:

1. the required baseline registers at low priority;
2. an evaluated optional provider may register the same capability at higher priority;
3. provider health/freshness is refreshed through the normal registry health path;
4. unavailable providers are removed from resolution and the baseline wins;
5. a task that explicitly requires a capability absent from every healthy provider fails normally.

## State classes

Provider state is classified as:

- **derived index**: rebuildable symbol/text/vector/dependency/index freshness state; non-canonical;
- **authored metadata**: deliberately accepted human/agent annotations; persistence/export/backup
  semantics are required before adoption;
- **telemetry**: bounded health/query/evaluation measurements; not project truth.

The shipped baseline owns no persistent index.

## Plugin and Registry requirements

An adopted enhanced provider should normally be packaged as a #20 capability-provider plugin. Its
manifest and optional #81 Registry item must disclose:

- exact upstream/source/version/license/provenance;
- capabilities and platform/interface compatibility;
- permissions, filesystem scope, network and secret requirements;
- CPU/RAM/storage/index expectations;
- cost status;
- trust/review/evaluation status;
- checksum/signature metadata where available;
- activation, disable, removal and cleanup behavior;
- whether persistent authored state exists.

Listing in the Registry never implies installation, activation, trust, secret access or repository
write authority.

## Candidate evaluation

ProjectAtlas, Codegraph, Graphify and Understand Anything are **candidates only**. Their maintained
successors, source repositories, licenses, costs, security model and current maintenance status must
be verified at evaluation time before a pilot.

Every pilot is compared against the deterministic baseline. At minimum record:

- time to useful context and representative task/first-pass success where available;
- tool calls, broad/full-file reads, repeated exploration and model-context bytes/tokens;
- source-slice/revision provenance accuracy;
- symbol/reference/dependency correctness where applicable;
- dirty-Workspace and incremental freshness behavior;
- initial/rebuild/update latency;
- query latency;
- CPU/RAM/disk/index growth;
- network/secret/external-service requirements;
- failure, disable, repair and rebuild behavior.

Marketing claims (including token savings) are not platform evidence until reproduced by this
evaluation. Unmeasured metrics remain explicitly unmeasured rather than being represented as zero.

## Security defaults

Third-party providers are untrusted until reviewed. Default policy is read-only source plus a
provider-owned cache/index directory, no repository writes, no worktree ownership, no root/sudo,
no unrestricted Docker socket, no global secrets, egress denied unless explicitly required,
bounded resources, recorded provenance, and a deterministic disable/remove/rebuild path.

## #502 completion boundary

The clarified provider-neutral v1 core is complete. The repository contains:

- the canonical repository-intelligence taxonomy;
- exact Repository and Run-bound Workspace provenance;
- deterministic required baseline behavior;
- CapabilityRegistry selection and deterministic fallback;
- production #82/#15 immutable-revision wiring;
- #37 Workspace Snapshot/live-materialization freshness including dirty-content evidence;
- bounded Context funnel and scoped Search federation integrations;
- heavy-work resource envelopes that fail closed until admission bounds are measured;
- an optional #20-compatible ProjectAtlas candidate shell with reversible lifecycle;
- safe optional technical Registry/catalog representation.

ProjectAtlas v0.4.5 has also completed a pinned checksum-verified, read-only, external-state,
no-network **evaluation** pilot. The final #502 workflow compares it against a real deterministic Git
reference baseline on the same immutable tiny fixture and records measured timing/tool-call/context-
byte/state deltas without fabricating unmeasured correctness metrics.

The final provider decision for #502 is **experimental/deferred**. ProjectAtlas is not the default
provider and its production repository map/search/source-slice/symbol/graph capabilities remain
disabled. The CI seccomp wrapper proves the evaluation boundary only; it is not treated as a generic
production sandbox.

That conservative provider decision does not keep the v1 core issue open. Future third-party
provider adoption, richer graph/semantic capability activation, version-pinned output normalizers,
representative large-repository benchmarks and production worker sandboxing belong in follow-up
work. They must not weaken or replace the shipped deterministic baseline.

See `docs/history/issues/ISSUE_502_COMPLETION.md` for the final acceptance record and
`REPOSITORY_INTELLIGENCE_PROJECTATLAS_PILOT.md` for the candidate evidence boundary.
