# Platform-wide Search

Issue #45 introduces Search as a progressive, derived discovery layer over canonical platform resources.

## Invariants

1. Search state is never canonical state.
2. Canonical resource IDs and types remain the primary identity in every result.
3. The search index must be rebuildable from canonical resource APIs/stores.
4. No baseline feature requires embeddings, a vector database or a paid search service.
5. `SearchProvider` is replaceable. Provider-private IDs are not canonical identities.
6. Raw provider results are not a northbound authorization boundary. The Control Plane authorizes candidates before returning results or counts to callers.
7. Search must not reveal inaccessible resource existence through snippets, result counts or facets.

## Foundation contracts

`ai_multi_agent_platform.search` provides:

- `SearchQuery`: backend-neutral free-text/exact-ID/filter/pagination contract;
- `SearchResult`: canonical result identity, safe display metadata, relevance and provenance;
- `SearchDocument`: derived index representation;
- `SearchProvider`: replaceable search/index boundary;
- `LocalSearchProvider`: dependency-free baseline implementation;
- `SearchService`: authorization-safe candidate filtering and canonical pagination;
- `document_from_resource(...)`: mapping from safe canonical Control Plane resources into derived documents.

The canonical vocabulary includes `semantic` and `hybrid` modes, but providers advertise what they actually support. The baseline local provider intentionally supports only exact, keyword and metadata modes and reports unsupported semantic/hybrid requests explicitly.

## Stage 1 Control Plane integration

The composed Control Plane exposes `GET /api/v1/search` and currently rebuilds/indexes the canonical resources that already have stable APIs:

- Projects;
- Workspaces;
- Tasks;
- Runs.

Supported Stage 1 query features include:

- exact canonical ID lookup;
- keyword/text search over safe indexed metadata;
- resource-type filtering;
- Project and Workspace scoping;
- status and tag filters;
- inclusive timezone-aware `updated_after` / `updated_before` filters where `updated_at` exists;
- source/provider filters;
- pagination and deterministic sorting;
- explicit unsupported-capability responses for semantic/hybrid modes on the local provider.

Later domains are added only after their owning canonical APIs exist. Search must not invent a second schema authority for Agents, Files, Knowledge, Connectors, Conversations, Verification, Organizations or future domains.

## CLI and frontend clients

Both user-facing clients consume the same canonical `GET /api/v1/search` endpoint. Neither client has a private index, backend-specific search path or frontend-only authorization filter.

### CLI

The platform CLI exposes:

```text
platform search [QUERY]
```

It supports the canonical Stage 1 filters directly, including:

- `--id` for exact canonical identity lookup;
- repeated or comma-separated `--type`, `--status`, `--tag`, `--source` and `--provider` values;
- `--project-id` and `--workspace-id` scopes;
- `--updated-after` and `--updated-before`;
- `--mode`, `--limit`, `--cursor`, `--sort` and `--direction`.

The CLI forwards these values to `/search` and leaves canonical validation and authorization to the Control Plane. A provider's `unsupported_capability` or canonical unavailable response is preserved rather than hidden or replaced by client-side behavior.

### Frontend

The existing `/search` navigation entry is backed by a global Search page that:

- calls only the typed Control Plane `search(...)` client method;
- exposes keyword/exact-ID, scope, type, status/tag, provenance, time, mode and pagination controls;
- renders only the authorization-filtered `SearchPage` returned by the Control Plane;
- clearly indicates when optional semantic/hybrid modes are unsupported;
- reuses canonical provider-unavailable error presentation;
- links known resource types to their canonical UI routes using the result's canonical type and ID.

Known Project, Workspace, Task, Run, Artifact, Result, Plan, Step, Model and Model-Provider results can navigate to their existing canonical UI route. Unknown future types are not assigned invented client routes; their canonical API reference remains visible until that domain's UI integration exists.

## Synchronization semantics

The provider boundary supports:

- `upsert(document)` for write-through or event-driven refresh;
- `delete(resource_type, resource_id)` for tombstone/deletion propagation;
- `rebuild(documents)` to replace the entire derived index from canonical sources.

The baseline local provider replaces documents by `(resource_type, resource_id)`, so updates do not create duplicate identities. `rebuild(...)` atomically replaces its in-memory derived state.

For the initial Stage 1 Control Plane path, the index is rebuilt from canonical sources before each query. This is intentionally correctness-first: updates and deletions cannot remain silently stale even without a durable index or event bus. Later durable providers may replace this with write-through/event-driven synchronization plus revision/checkpoint metadata, but that metadata remains provider provenance and never becomes canonical resource state.

## Authorization placement

Authorization-aware search is a Control Plane/application concern above the raw `SearchProvider`.

The request path is:

```text
CLI / Frontend / Agent
    -> Control Plane Search API
    -> SearchProvider candidate discovery
    -> canonical authorization checks for each candidate
    -> authorization-safe result filtering/counting/snippets
    -> canonical SearchResult page
```

The baseline `SearchService` scans provider candidates and applies canonical authorization before calculating the caller-visible `total`, cursor or result snippets. A provider may later use precomputed scope metadata as an optimization, but this must not replace canonical authorization.

## Failure and degraded behavior

- A SearchProvider outage is returned through the canonical error contract (for example `503 unavailable` when retryable).
- Unsupported optional query modes return `unsupported_capability` rather than silently changing semantics.
- The CLI preserves those canonical errors.
- The frontend distinguishes optional-mode degradation from general request/provider failures.
- The baseline has no dependency on Registry connectivity, embeddings, vector databases or paid search services.

## Remaining #45 integrations

The secure Stage 1 surface plus CLI/frontend clients establish one canonical discovery path. Future work in #45 can add:

- progressive indexing for Agents/Teams, Models/Capabilities, Files/Memory/Knowledge, Connectors, Conversations, Verification, Organizations, Templates and other domains after their canonical APIs are available;
- richer domain-specific filters such as Task priority/deadline/assignment once owned by the corresponding canonical domain;
- durable/event-driven indexing and stale-index checkpoints for larger deployments;
- optional semantic/hybrid provider adapters without making them baseline requirements.
