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

### Task-management projection and filters

Tasks are indexed from the same managed northbound Task projection used by `/api/v1/tasks`, not from a Search-owned copy of Task planning state. Search therefore consumes the canonical #88 Task-management metadata and derived queue state directly while the platform-owned Task lifecycle remains authoritative.

The Task Search projection supports:

- canonical priority values `low`, `normal`, `high` and `urgent`;
- inclusive timezone-aware `due_after` / `due_before` windows over canonical `due_at`;
- `assigned` / `unassigned` filtering based on canonical responsibility or Agent/AgentTeam assignment;
- exact `responsible_id` filtering;
- exact canonical `agent_assignment_id` filtering;
- `blocked=true|false` using the canonical effective Task-management/lifecycle projection;
- `overdue=true|false` using the canonical #88 derived overdue state;
- exact dependency Task discovery through `dependency_id`;
- labels through the existing Search `tag` filter.

Safe Task-management relationship metadata such as responsibility, Agent assignment, parent/dependency references and effective blocking reason can participate in keyword discovery. Arbitrary `resource_hints` and other nested planning payloads are not promoted into global Search text.

These fields exist only on derived `SearchDocument` metadata used for filtering. The generic `SearchResult` contract remains resource-neutral, and Search does not create a second Task-management schema or persistence layer. Invalid priority values, malformed dependency IDs, invalid booleans, invalid assignment state and reversed/non-timezone-aware due windows are rejected by the canonical Search query boundary.

### Task reference resources

The rebuild also derives the existing canonical Task reference resources exposed by the Control Plane:

- Plans;
- Steps;
- Artifacts;
- Results.

Search does not create a second Plan, Step, Artifact or Result store. These documents are rebuilt from the same canonical Task state and the same northbound reference projections used by `/api/v1/plans`, `/api/v1/steps`, `/api/v1/artifacts` and `/api/v1/results`.

For an unambiguous reference attached to one Task, safe relationship metadata can be used for discovery:

- `task_id` for Plan, Step, Artifact and Result references;
- `plan_id` for Step references;
- `step_ids` for Plan references;
- parent Task owner and Project scope for authorization-aware Project filtering.

Reference results reuse the existing plural Control Plane authorization actions: `plans:list`, `steps:list`, `artifacts:list` and `results:list`. Exact-ID lookup, keyword results and caller-visible totals all pass through those canonical checks.

A canonical reference ID can theoretically be attached to more than one Task. Search therefore tracks every Task scope associated with that `(resource_type, resource_id)` identity. When multiple attachments exist, the derived document is deliberately reduced to scope-neutral ID/type metadata: parent Task IDs, owner information and Project relationships are not indexed or exposed. Authorization succeeds only if at least one canonical attachment is visible to the caller. This preserves discoverability of the shared canonical identity without leaking relationships from another Task or Project.

### Progressive registered domains

Canonical domains exposed through the generic Control Plane `ResourceService` registration seam are automatically eligible for Search. The registered service remains the domain/schema authority; Search reads its canonical northbound resource shape, validates it through the same private-field leak checks used by the Control Plane, derives a `SearchDocument`, and retains the registered collection in result provenance.

The current progressive path supports, when those domain services are registered:

- Agents;
- Agent Teams;
- Agent Runs;
- Capabilities;
- Capability Providers;
- Automations;
- Automation Deliveries;
- Approvals;
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

### Automations and Automation Deliveries

Issue #18 registers canonical `automations` and `automation-deliveries` resources through the same progressive `ResourceService` seam. Search therefore does not need a second Automation repository, Automation-specific index provider or separate identity model.

The hardened Automation Control Plane adds owner and Project/Workspace scope to the northbound resources before they enter Search. The derived Search projection supports safe Automation metadata including:

- Automation name and description;
- Automation state as Search status;
- revision and `updated_at` where available;
- canonical Project/Workspace and owner scope;
- Delivery status;
- Delivery-to-Automation relationship through canonical `automation_id`;
- safe Delivery `trigger_type` and `source` keyword discovery.

Automation Search reuses the canonical registered-domain authorization actions `automation:list` and `automation-delivery:list`. Exact-ID lookup, Project filters, keyword matches and caller-visible totals therefore pass through the same authorization boundary as the corresponding canonical resources. A caller without access to another Project cannot infer Automation or Delivery existence through IDs, names, owner metadata, counts, snippets or the Delivery-to-Automation relationship.

### Approvals

The #15 security domain exposes canonical Approval inspection through the registered `approvals` ResourceService. Search consumes that safe northbound projection rather than reading Approval storage or proposed-action payloads directly.

Approval Search includes only discovery metadata needed to locate an authorized lifecycle record:

- canonical Approval ID and status;
- safe display identity from `subject_type` and `subject_id`;
- Project and owner scope;
- exact subject/resource identity;
- requested action and risk classification;
- Task, Run and capability references where present.

The global Search index deliberately does **not** include proposed payload values, requested-action digests, `payload_ref`, Approval reason text or decision comments. Those remain available only through the richer owning Approval API when the caller is authorized to inspect that resource.

Approval candidates reuse the registered-domain `approval:list` authorization action with candidate owner and Project scope. Unauthorized Approvals are removed before caller-visible totals, exact-ID results, snippets or relationship matches are calculated. Knowing a hidden Approval ID, Task ID or Project ID therefore does not reveal the hidden Approval through Search.

Search is discovery-only: an Approval Search result or its displayed status never authorizes, approves, rejects or otherwise mutates the canonical Approval lifecycle.

## Query features

The current canonical query supports:

- exact canonical ID lookup;
- keyword/text search over safe indexed metadata;
- resource-type filtering;
- Project and Workspace scoping;
- status and tag filters;
- inclusive timezone-aware `updated_after` / `updated_before` filters where `updated_at` exists;
- Task priority, due-window, assignment, responsibility, Agent assignment, blocked/overdue and dependency filters;
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
- repeated or comma-separated `--priority` values;
- `--due-after` and `--due-before`;
- `--assignment-state`, `--responsible-id` and `--agent-assignment-id`;
- `--blocked`, `--overdue` and `--dependency-id`;
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

Known Project, Workspace, Task, Run, Artifact, Result, Plan, Step, Model, Model-Provider and Approval results can navigate to their existing canonical UI route. Unknown or newly indexed types are not assigned invented client routes; their canonical API reference remains visible until that domain's UI integration exists.

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

The secure foundation, task-reference search, progressive registration bridge, Model/Capability inventory support, Automation/Approval discovery, Task-management filtering and CLI/frontend clients establish one canonical discovery path. Remaining progressive work includes, as the corresponding canonical APIs and privacy contracts are ready:

- policy-permitted Events where useful for global discovery;
- Files, scoped Memory and Knowledge;
- Nodes/Workers;
- Evaluations;
- Plugins/Extensions and Connectors/external-resource references;
- Conversations/Messages with retention/deletion propagation;
- Notifications and usage/resource summaries where useful;
- Templates and Repository/Git references;
- Verification resources;
- Organizations/Memberships with membership-removal isolation;
- durable/event-driven indexing, batching and stale-index checkpoints for larger deployments;
- optional semantic/hybrid provider adapters without making them baseline requirements.

External Registry/Marketplace search remains a separate optional distribution concern and is not required for local platform Search.
