# Agent/Team portability and import preview

This document extends the portable package foundation from issue #79 with the first concrete canonical resource codecs and a mutation-free import planner.

## Agent and Agent Team resource shape

Portable Agent and Agent Team resources use the resource types `agent` and `agent_team` and schema version `1`. Each resource contains:

- the stable canonical definition;
- every immutable revision from revision 1 through the definition's current revision;
- canonical profile/configuration data only;
- no `AgentRunRecord`, orchestrator runtime reference, provider-private session or other live execution state.

The portable resource version is the current canonical revision number. Revision histories must be contiguous and the definition must point at the final exported revision.

`AgentPortableSnapshot` and `AgentTeamPortableSnapshot` are transport-side snapshots used by the codecs. They do not become a second Agent repository or lifecycle authority.

## Dependency extraction

Agent exports declare dependencies found across the complete exported revision history rather than only the latest revision. The initial codec reports:

- explicit model configuration requirements;
- capability constraints, preserving required/optional semantics and version constraints;
- Knowledge Source references;
- Memory configuration references;
- Project and Workspace scope references where present.

Agent Team exports report:

- member Agent resources and the minimum referenced Agent revision;
- shared capability requirements;
- Project and Workspace scope references where present.

Canonical resource dependencies use the portable identifier convention:

```text
<resource_type>:<resource_id>
```

This is a package dependency encoding only. It does not replace canonical resource IDs.

## Reference remapping

The import preview produces one explicit mapping from `(resource_type, source_id)` to the selected target ID. Deserializers receive that mapping through `ImportContext`.

The Agent codec remaps:

- Agent ID;
- Project/Workspace scope IDs;
- explicit model configuration ID when a model resource mapping exists;
- Knowledge Source IDs;
- Memory configuration references.

The Agent Team codec remaps:

- Team ID;
- member Agent IDs;
- delegation target Agent IDs;
- leader Agent ID;
- Project/Workspace scope IDs.

Security principals/owners are not silently remapped by the codec. Opaque `shared_resource_refs` are also not guessed or rewritten without a typed resource identity. Later import-policy work must resolve ownership/security implications explicitly rather than treating data portability as authorization.

## Dry-run import preview

`ImportPreviewService` verifies package integrity and then calculates target behavior without calling any mutation API.

The preview contains:

- one planned source/target ID pair per resource;
- a deterministic package-local ID mapping for `regenerate` imports;
- preserved IDs for `preserve` and `historical_preserve` resources;
- existing-ID conflicts;
- optional caller-supplied name conflicts;
- required missing dependencies;
- optional missing dependencies;
- a dependency-safe topological import order;
- dependency-cycle conflicts;
- a final `ready` flag.

A preview is blocked when a required dependency is missing or a conflict exists. Optional missing dependencies are reported but do not by themselves block the preview.

## Deterministic clone IDs

For `IdPolicy.REGENERATE`, the reference planner derives the target UUID from:

- package checksum;
- resource type;
- source resource ID;
- allocation attempt.

When the source ID uses the platform's canonical `<prefix>_<uuid>` shape, the generated target preserves that prefix. The result is deterministic for the same sealed package and target availability state. A deployment may inject another allocator, but the chosen mapping remains explicit in the preview and is the only mapping supplied to codecs.

## Mutation boundary

This slice intentionally stops before repository mutation. A later issue #79 slice must consume only a successful preview and add:

- authorization/privacy validation;
- dependency/version compatibility enforcement beyond presence checks;
- dependency-ordered writes;
- rollback/recovery semantics;
- preserved import provenance and an import result report.

Until that boundary exists, deserialization reconstructs canonical snapshots in memory but does not install them into `AgentRepository`.
