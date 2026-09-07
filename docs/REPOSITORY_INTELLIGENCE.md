# Repository / Code Intelligence

Issue #502 adds repository/code intelligence as an **optional derived capability layer**. It does
not create a second Repository, Git, Workspace, Search, Task, Agent or authorization authority.

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
| `repository.map` | bounded file map at an exact revision |
| `repository.text_search` | deterministic baseline text search |
| `repository.source_slice` | exact bounded source lines |
| `repository.health` | provider health |
| `repository.index_status` | freshness/index state |

Future plugins may add compatible symbol/reference/dependency/impact/semantic/domain capabilities.
Candidate-specific types must remain behind the capability/plugin boundary.

Every source-derived result includes:

- canonical repository ID;
- requested revision;
- immutable resolved revision;
- intelligence provider ID;
- normalized freshness evidence.

Provider summaries never outrank current source, tests or canonical repository state.

## Deterministic baseline

`BaselineRepositoryIntelligenceProvider` is deliberately small and dependency-free. It consumes an
injected exact `RepositoryTree` snapshot loader, performs deterministic bounded map/text/slice
operations in-process, and owns no persistent index.

Production composition must supply a loader through the existing #82/#37 policy/materialization
boundary. The baseline provider must never be handed a provider-private local path merely to bypass
that boundary.

The reference provider is the portability floor. Local deployments may later supply an optimized
Git/ripgrep/LSP-backed plugin, but Git/ripgrep/LSP remains an optimization/reference-tool choice,
not a new canonical data model. LSP/symbol semantics are not fabricated when no LSP is configured.

## Provider selection and fallback

All implementations publish the same canonical `CapabilitySpec` for a capability/version.
`CapabilityRegistry` therefore owns selection:

1. a baseline provider registers at low priority;
2. an evaluated optional provider may register the same capability at higher priority;
3. provider health/freshness is refreshed through the normal registry health path;
4. unavailable providers are removed from resolution and the baseline wins;
5. a task that explicitly requires a capability absent from every healthy provider fails normally.

A provider with a persistent index must map a stale index to unavailable for capabilities whose
correctness depends on freshness. It may remain healthy for independent operations such as health
or rebuild/status reporting, but must not return stale source-derived data as if it represented the
current revision.

## State classes

Provider state is classified as:

- **derived index**: rebuildable symbol/text/vector/dependency/index freshness state; non-canonical;
- **authored metadata**: deliberately accepted human/agent annotations; persistence/export/backup
  semantics are required before adoption;
- **telemetry**: bounded health/query/evaluation measurements; not project truth.

The baseline owns no persistent index and reports live-revision freshness for source-derived
results.

## Plugin and Registry requirements

An adopted provider should normally be packaged as a #20 capability-provider plugin. Its manifest
and optional #81 Registry item must disclose:

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

- time to useful context and representative task/first-pass success;
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
evaluation.

## Security defaults

Third-party providers are untrusted until reviewed. Default policy is read-only source plus a
provider-owned cache/index directory, no repository writes, no worktree ownership, no root/sudo,
no unrestricted Docker socket, no global secrets, egress denied unless explicitly required,
bounded resources, recorded provenance, and a deterministic disable/remove/rebuild path.

## Foundation scope

This foundation establishes the taxonomy, source-provenance contract, deterministic no-index
provider and CapabilityRegistry fallback behavior. Follow-up work for #502 must still wire a
production policy-enforced snapshot loader, exercise dirty Workspace freshness, evaluate current
third-party candidates, package accepted adapters/plugins, add resource-pressure evidence and
publish optional Registry metadata. The issue should remain open until those acceptance items are
satisfied.
