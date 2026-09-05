# Memory and Knowledge in global Search

Issue #290 integrates the canonical Memory and Knowledge lifecycles from #251 into the global Search foundation from #45.

## Authority boundary

Global Search is a derived discovery index only. It does not become canonical storage for Memory or Knowledge and does not replace:

- Memory ranking, retrieval, retention, supersession or expiry semantics;
- Knowledge ingestion, re-indexing, retrieval, citation or source lifecycle semantics;
- the canonical authorization and lifecycle contracts owned by the data providers.

A full Search rebuild must be possible from canonical Memory entries and Knowledge sources/documents alone.

## Discovery snapshots

`MemoryProvider.list_entries_for_discovery()` and the Knowledge discovery enumeration seams provide actor-independent canonical snapshots for rebuilding derived Search documents. These methods do not expose Search results directly and are not northbound retrieval APIs.

Providers that do not implement discovery enumeration, or whose discovery snapshot is temporarily unavailable, degrade fail-closed for that Search collection. A rebuild therefore removes stale derived documents instead of retaining potentially unauthorized data.

## Privacy-minimized projections

### Memory

The Search projection may contain:

- canonical Memory ID;
- scope and scope ID;
- canonical owner/project context required for authorization;
- creation/expiry metadata;
- origin and retention class;
- canonical provenance references;
- safe aliases derived from scope/origin/retention.

It deliberately excludes:

- the Memory value/content;
- arbitrary Memory metadata;
- classification payload details;
- provenance locations/checksums;
- embeddings, vector state and provider-native index identifiers.

Expired, deleted and superseded entries are not emitted by the reference discovery snapshot.

### Knowledge source

The Search projection may contain canonical source ID, title, project/owner scope, revision, lifecycle status and timestamps. It excludes arbitrary source metadata, content checksums and provider-native indexing information.

Removed sources are not indexed.

### Knowledge document

Knowledge documents are searchable as subordinate canonical metadata resources. The Search projection contains canonical document/source IDs, source title, revision, project/owner scope, lifecycle status and timestamps.

It deliberately excludes document content, backend locations, checksums, embeddings, vector collections and provider-native index identifiers. Only the document for the source's current canonical revision is emitted; stale revisions remain outside global Search.

Knowledge retrieval results remain query-scoped and `search_indexable = False`, so citation/content semantics stay in the Knowledge provider.

## Authorization

Discovery enumeration is actor-independent so a full index can be rebuilt deterministically. Authorization is applied by the Control Plane before Search results, exact-ID results and total counts are returned.

The Search projection carries enough canonical scope to authorize:

- User Memory as `user:<scope_id>`;
- Agent Memory as `agent:<scope_id>`;
- Workspace Memory through canonical `project_id`;
- Organization Memory as `organization:<scope_id>` plus organization provenance;
- Knowledge sources/documents through their canonical owner/project scope.

A denied exact ID is indistinguishable from a missing result at the Search boundary.

## Lifecycle propagation

Because Search is rebuilt from current canonical snapshots:

- Memory supersession removes the previous entry and exposes the replacement;
- Memory deletion/expiry removes the derived document;
- Knowledge re-indexing replaces the stale document revision with the current one;
- Knowledge source removal removes both the source and subordinate Search document;
- a full rebuild reproduces the derived index without relying on provider-private Search/vector state.

The reference implementation intentionally favors deterministic rebuild correctness over introducing a second event-sourced lifecycle authority inside Search.
