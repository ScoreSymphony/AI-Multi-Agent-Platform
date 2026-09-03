# Platform-wide Search

Issue #45 introduces Search as a progressive, derived discovery layer over canonical platform resources.

## Invariants

1. Search state is never canonical state.
2. Canonical resource IDs and types remain the primary identity in every result.
3. The search index must be rebuildable from canonical resource APIs/stores.
4. No baseline feature requires embeddings, a vector database or a paid search service.
5. `SearchProvider` is replaceable. Provider-private IDs are not canonical identities.
6. Raw provider results are not a northbound authorization boundary. The Control Plane must authorize/filter candidates before returning results or counts to callers.
7. Search must not reveal inaccessible resource existence through snippets, result counts or facets.

## Foundation contracts

`ai_multi_agent_platform.search` provides:

- `SearchQuery`: backend-neutral free-text/exact-ID/filter/pagination contract;
- `SearchResult`: canonical result identity, safe display metadata, relevance and provenance;
- `SearchDocument`: derived index representation;
- `SearchProvider`: replaceable search/index boundary;
- `LocalSearchProvider`: dependency-free baseline implementation;
- `document_from_resource(...)`: mapping from safe canonical Control Plane resources into derived documents.

The canonical vocabulary includes `semantic` and `hybrid` modes, but providers advertise what they actually support. The baseline local provider intentionally supports only exact, keyword and metadata modes and reports unsupported semantic/hybrid requests explicitly.

## Initial searchable domains

The first Control Plane integration should rebuild/index resources that already have canonical APIs:

- Projects;
- Workspaces;
- Tasks;
- Runs;
- optionally Plans/Steps/Artifacts/Results where their current metadata is useful and safe.

Later domains are added only after their owning canonical APIs exist. Search must not invent a second schema authority for Agents, Files, Knowledge, Connectors, Conversations, Verification, Organizations or future domains.

## Synchronization semantics

The provider boundary supports:

- `upsert(document)` for write-through or event-driven refresh;
- `delete(resource_type, resource_id)` for tombstone/deletion propagation;
- `rebuild(documents)` to replace the entire derived index from canonical sources.

The baseline local provider replaces documents by `(resource_type, resource_id)`, so updates do not create duplicate identities. `rebuild(...)` atomically replaces its in-memory derived state.

Future durable providers may persist index revision/checkpoint metadata, but that metadata remains provider provenance and must never become canonical resource state.

## Authorization placement

Authorization-aware search is a Control Plane/application concern above the raw `SearchProvider`.

The required request path is:

```text
Client / Agent
    -> Control Plane Search API
    -> canonical authorization/resource-discovery checks
    -> SearchProvider candidate discovery
    -> authorization-safe result filtering/counting/snippets
    -> canonical SearchResult page
```

A provider may use precomputed scope metadata as an optimization, but this must not replace canonical authorization. The Control Plane must not expose the provider's raw `total` or facets when doing so could reveal unauthorized resources.

## Next implementation slice

The next slice for #45 should add the Control Plane search service/endpoint and a canonical rebuild operation that gathers current Project/Workspace/Task/Run resources. That integration must include tests for:

- authorized and unauthorized discovery;
- private-resource non-disclosure through result counts;
- exact Task/Project/Run lookup;
- project scoping;
- rebuild from canonical sources;
- provider unavailable/degraded behavior;
- update/delete propagation from canonical lifecycle changes.

CLI and frontend surfaces should consume that Control Plane endpoint later rather than calling a SearchProvider directly.
