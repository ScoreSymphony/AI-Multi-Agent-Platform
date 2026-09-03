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

## Current Control Plane integration

The composed Control Plane exposes `GET /api/v1/search` and rebuilds a single derived index from the canonical sources that are currently available.

### Foundation resources

The foundation rebuild covers:

- Projects;
- Workspaces;
- Tasks;
- Runs.

These resources use their existing canonical list/discovery authorization actions before a Search result becomes caller-visible.

### Progressive registered domains

Canonical domains exposed through the generic Control Plane `ResourceService` registration seam are automatically eligible for Search. The registered service remains the domain/schema authority; Search reads its canonical northbound resource shape, validates it through the same private-field leak checks used by the Control Plane, derives a `SearchDocument`, and retains the registered collection in result provenance.

The current progressive path supports, when those domain services are registered:

- Agents;
- Agent Teams;
- Agent Runs;
- Capabilities;
- Capability Providers;
- other future registered canonical collections that satisfy the same contract.

Registered Search results reuse the collection's canonical `<singular>:list` authorization action. Candidate owner and Project scope are forwarded into that authorization decision so two resources in the same collection can still have different visibility. Unauthorized registered resources are removed before caller-visible counts, snippets, cursors or exact-ID results are calculated.

A registered resource type may not collide with a built-in Search resource type or ambiguously map to multiple canonical collections.

### Models and Model Providers

When a canonical `ModelRegistry` is configured, Search also rebuilds:

- Models;
- Model Providers.

These are built-in Control Plane inventories rather than registered extension collections, so they are derived directly from the canonical Model Registry using the same safe northbound resource projections as `/api/v1/models` and `/api/v1/model-providers`.

Model Search supports safe inventory metadata such as:

- canonical model configuration ID and display name;
- canonical aliases;
- canonical provider relationship;
- deployment location;
- revision;
- effective health/status;
- enabled/disabled metadata and safe routing capability vocabulary where exposed.

Model results are authorized with `model:list`; Model Provider results use `model-provider:list`. Provider-native model identifiers and `adapter_metadata` are deliberately not indexed. They therefore cannot become Search identities, keywords, snippets or caller-visible Search provenance.

Capability inventory follows the registered-domain path. `capability_resource_services(...)` exposes the canonical #12 `capabilities` and `capability-providers` collections, so those resources use the same progressive rebuild and authorization flow without Search depending directly on the Capability Registry implementation.

## Query features

The current canonical query supports:

- exact canonical ID lookup;
- keyword/text search over safe indexed metadata;
- resource-type filtering;
- Project and Workspace scoping;
- status and tag filters;
- inclusive timezone-aware `updated_after` / `updated_before` filters where `updated_at` exists;
- source/provider filters;
- pagination and deterministic sorting;
- explicit unsupported-capability responses for semantic/hybrid modes on the local provider.

Later domains are added only after their owning canonical APIs exist. Search must not invent a second schema authority for Files, Memory, Knowledge, Connectors, Conversations, Verification, Organizations or future domains.

## CLI and frontend clients

Both user-facing clients consume the same canonical `GET /api/v1/search` endpoint. Neither client has a private index, backend-specific search path or frontend-only authorization filter.

### CLI

The platform CLI exposes:

```text
platform search [QUERY]
```

It supports the canonical filters directly, including:

- `--id` for exact canonical identity lookup;
- repeated or comma-separated `--type`, `--status`, `--tag`, `--source` and `--provider` values;
- `--project-id` and `--workspace-id` scopes;
- `--updated-after` and `--updated-before`;
- `--mode`, `--limit`, `--cursor`, `--sort` and `--direction`.

The CLI forwards these values to `/search` and leaves canonical validation and authorization to the Control Plane. A provider's `unsupported_capability` or canonical unavailable response is preserved rather than hidden or replaced by client-side behavior.

### Frontend

The `/search` navigation entry is backed by a global Search page that:

- calls only the typed Control Plane `search(...)` client method;
- exposes keyword/exact-ID, scope, type, status/tag, provenance, time, mode and pagination controls;
- renders only the authorization-filtered `SearchPage` returned by the Control Plane;
- clearly indicates when optional semantic/hybrid modes are unsupported;
- reuses canonical provider-unavailable error presentation;
- links known resource types to their canonical UI routes using the result's canonical type and ID.

Known Project, Workspace, Task, Run, Artifact, Result, Plan, Step, Model and Model-Provider results can navigate to their existing canonical UI route. Unknown or newly indexed types are not assigned invented client routes; their canonical API reference remains visible until that domain's UI integration exists.

## Synchronization semantics

The provider boundary supports:

- `upsert(document)` for write-through or event-driven refresh;
- `delete(resource_type, resource_id)` for tombstone/deletion propagation;
- `rebuild(documents)` to replace the entire derived index from canonical sources.

The baseline local provider replaces documents by `(resource_type, resource_id)`, so updates do not create duplicate identities. `rebuild(...)` atomically replaces its in-memory derived state.

For the current correctness-first Control Plane path, the index is rebuilt from canonical sources before each query. Updates and deletions therefore cannot remain silently stale even without a durable index or event bus. Later durable providers may replace this with write-through/event-driven synchronization plus revision/checkpoint metadata, but that metadata remains provider provenance and never becomes canonical resource state.

A rebuild tolerates absent optional domains: for example, Search remains functional when no Model Registry or no optional registered ResourceService is configured.

## Authorization placement

Authorization-aware Search is a Control Plane/application concern above the raw `SearchProvider`.

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

Exact-ID lookup follows the same rule: knowing or guessing a canonical ID does not reveal whether an unauthorized resource exists.

## Failure and degraded behavior

- A SearchProvider outage is returned through the canonical error contract (for example `503 unavailable` when retryable).
- Unsupported optional query modes return `unsupported_capability` rather than silently changing semantics.
- The CLI preserves those canonical errors.
- The frontend distinguishes optional-mode degradation from general request/provider failures.
- The baseline has no dependency on Registry connectivity, embeddings, vector databases or paid search services.

## Remaining #45 integrations

The secure foundation, progressive registration bridge, Model/Capability inventory support and CLI/frontend clients establish one canonical discovery path. Remaining progressive work includes, as the corresponding canonical APIs and privacy contracts are ready:

- additional core resources such as Plans, Steps, Artifacts, Results and policy-permitted Events where not yet included in the rebuild;
- Files, scoped Memory and Knowledge;
- Nodes/Workers;
- Approvals, Automations and Evaluations;
- Plugins/Extensions and Connectors/external-resource references;
- Conversations/Messages with retention/deletion propagation;
- Notifications and usage/resource summaries where useful;
- Templates and Repository/Git references;
- Verification resources;
- Organizations/Memberships with membership-removal isolation;
- richer Task filters such as priority, deadline, assignment, labels and dependency state once owned by the canonical Task domain;
- durable/event-driven indexing, batching and stale-index checkpoints for larger deployments;
- optional semantic/hybrid provider adapters without making them baseline requirements.

External Registry/Marketplace search remains a separate optional distribution concern and is not required for local platform Search.
