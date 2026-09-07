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
- Runs;
- canonical Task lifecycle Events.

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

### Canonical Events

Task lifecycle Events are derived from the canonical Event repository while rebuilding Search. Search does not create a second event history or treat event results as lifecycle authority.

The Search document keeps the canonical Event ID and safe event metadata while inheriting the parent Task owner and Project scope. Event candidates are authorized through `event:list` against the canonical Task scope before result counts or exact-ID matches become visible. Their canonical navigation remains the owning Task timeline rather than an invented Search-owned Event endpoint.

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
- Files;
- Plugins;
- Plugin Candidates;
- Usage Aggregates;
- Usage Budgets;
- Evaluation Suites;
- Evaluation Runs;
- Verification Policies, Requests and Results;
- Connector Definitions;
- privacy-safe Connections;
- other future registered canonical collections that satisfy the same contract.

Registered Search results reuse the collection's canonical `<singular>:list` authorization action. Candidate owner and Project scope are forwarded into that authorization decision so two resources in the same collection can still have different visibility. Unauthorized registered resources are removed before caller-visible counts, snippets, cursors or exact-ID results are calculated.

A registered resource type may not collide with a built-in Search resource type or ambiguously map to multiple canonical collections.

A registered `ResourceService` may opt out of global Search by exposing `search_indexable = False`. Actor-filtered domains that require a complete rebuild may expose the internal `list_search_resources()` rebuild seam while leaving normal northbound listing semantics unchanged. This separation is used only to reconstruct derived Search state; it does not bypass the per-result authorization boundary.

### Files

Issue #13 exposes a canonical `FileProvider` with project-scoped metadata enumeration. The Control Plane adds a read-only `files` ResourceService that projects `FileRecord` metadata into the same registered-domain seam used by global Search. Search does not read provider databases or filesystem paths directly and never indexes File bytes.

The File projection exposes safe canonical metadata including:

- canonical File ID;
- Project scope;
- canonical owner reference;
- lifecycle state such as `ready`;
- content type;
- size and checksum metadata on the owning `/files` API surface;
- canonical Artifact relationships.

Global Search deliberately limits searchable File text to safe discovery fields such as canonical identity, Project/owner scope, lifecycle state, content type and Artifact IDs. Arbitrary `FileRecord.metadata` values are not promoted into Search keywords or snippets, so provider/application metadata does not silently become globally discoverable text.

A complete rebuild enumerates the unscoped File namespace plus the canonical Project IDs supplied by the composition root. The underlying `FileProvider` remains responsible for its #13 scope semantics, while Search applies the normal per-result `file:list` authorization check with the candidate owner and Project context before a result, count or exact-ID match becomes caller-visible.

The canonical `/files` read surface uses the same scope principle. The File ResourceService opts into per-resource authorization so an unscoped list is filtered by each candidate's canonical owner/Project context before pagination and counts are calculated. Direct reads re-check `file:read` with the resolved File scope; a scope-denied read is returned as neutral `not_found` rather than revealing that the File exists in another Project.

Tombstoned Files are excluded by the canonical FileProvider. Because the correctness-first Search path rebuilds from canonical sources before a query, a deleted/tombstoned File disappears from Search without a Search-owned deletion state or second lifecycle.

Memory and Knowledge are intentionally not derived from private provider internals. The current Memory/Knowledge contracts do not yet provide the privacy-aware globally enumerable northbound content lifecycle required for Search. Global Search waits for that canonical contract rather than using implementation-private provider methods. The follow-up content lifecycle/enumeration work is tracked separately, including #251.

### Plugins and Plugin Candidates

Issue #20 exposes canonical installed Plugin lifecycle state through the registered `plugins` ResourceService and discovery candidates through `plugin-candidates`. Global Search consumes those existing northbound resources through the progressive registration seam; it does not create another Plugin registry, catalog, persistence layer, installation source or Plugin identity.

The Search projection keeps useful flat discovery metadata searchable, including:

- Plugin or Candidate canonical ID, name and description;
- Plugin version and manifest version;
- author;
- installed Plugin lifecycle state and compatibility where available;
- install source;
- declared capability IDs;
- extension IDs and extension types;
- requested and granted permission identifiers;
- string dependency IDs;
- configured/unconfigured state for installed Plugins.

The generic Search result `version` uses canonical `plugin_version` when a conventional revision field is not available. Candidate results retain `/api/v1/plugin-candidates/{id}` as their canonical reference; installed Plugins retain `/api/v1/plugins/{id}`.

The nested Plugin Manifest is deliberately not flattened into global Search. Runtime entrypoints, source-repository URLs, configuration schemas, extension metadata and other nested manifest structures therefore do not become Search keywords merely because they are inspectable through the richer canonical #20 Plugin API. Search indexes only the explicitly permitted flat discovery projection.

Plugin Candidates reuse `plugin-candidate:list`; installed Plugins reuse `plugin:list`. Denied candidates and Plugins are removed before caller-visible counts, snippets, cursors and exact-ID results are calculated, so knowing a hidden Plugin ID or name does not make its existence observable through Search.

Search remains discovery-only. Finding a Plugin or Candidate never installs, configures, enables, disables, updates or removes it; those lifecycle transitions continue to require the canonical #20 command surface and its authorization/approval rules.

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

### Usage Aggregates and Budgets

Issue #76 exposes canonical accounting state through registered `usage-records`, `usage-aggregates` and `usage-budgets` ResourceServices. Global Search intentionally does **not** index raw `usage-records`: they are high-cardinality accounting records and remain available through the owning accounting API instead of turning global Search into a telemetry query engine.

The `usage-aggregates` and `usage-budgets` services provide complete Search rebuild enumerators while their normal northbound reads remain owner-isolated. Searchable metadata is limited to safe flat discovery vocabulary such as:

- metric type and unit;
- aggregation mode;
- canonical scope type/ID where exposed;
- budget kind/action/window mode;
- threshold level/status where available;
- canonical owner scope.

Nested trend points, quality-count structures, raw quantities/cost evidence and arbitrary provenance are not recursively flattened into Search text. Per-result authorization still happens before caller-visible counts and exact-ID results.

### Evaluation Suites and Runs

Issue #19 exposes canonical `evaluation-suites` and `evaluation-runs` through registered Control Plane ResourceServices. Search consumes those northbound shapes only; it does not query Evaluation repositories, runners, evaluators or provider internals directly.

The safe discovery projection includes:

- Evaluation Suite name and description;
- Suite tags, canonical `suite_id` and exact version;
- Evaluation Run `suite_id` and `suite_version`;
- Run lifecycle status;
- optional `baseline_run_id`;
- start/completion time for time filtering.

Suite version is mapped into the generic Search result version. Run `completed_at`, falling back to `started_at`, supplies the generic Search time projection.

Search deliberately does not traverse nested Suite cases, fixtures, input templates, assertions, metrics or rubrics. Run snapshot environment/configuration references and nested result/comparison evidence likewise remain outside global Search text. Evaluation candidates reuse `evaluation-suite:list` and `evaluation-run:list`; authorization is applied before totals, snippets or exact-ID results become visible.

Search is discovery-only and does not determine Evaluation outcomes. See `docs/SEARCH_EVALUATIONS.md` for the focused integration contract.

### Connector Definitions and Connections

Issue #44 exposes canonical Connector Definitions and Connections through the existing `register_connector_control_plane(...)` ResourceService registration path. Search does not create a parallel connector catalog or query adapters/remote services directly.

The northbound resources carry explicit canonical types:

- `connector-definition` from `connector-definitions`;
- `connection` from `connections`.

Connector Definition Search uses safe flat metadata such as canonical type/version, name/description, supported operations, features, resource types, actions and event types. Nested configuration schemas, health semantics, authentication metadata and source/adapter metadata are not recursively flattened into global Search.

Connection Search uses a dedicated complete rebuild projection because ordinary `/connections` listing is actor-filtered. The Search rebuild projection contains only safe discovery state such as canonical Connection identity, display name, connector type/version, owner/Project scope, requested/granted scope identifiers, enabled state, lifecycle status/health, timestamps and revision. It deliberately excludes:

- `SecretReference` values and IDs;
- endpoint metadata;
- account/adapter metadata;
- provider-native account identifiers;
- arbitrary remote payloads.

Per-result `connection:list` authorization still occurs above the derived index before caller-visible counts or exact-ID matches. The rebuild enumerator therefore reconstructs candidate state but is not an authorization bypass.

Organization-scoped Connections are intentionally excluded from global Search until #87 supplies stable membership/removal/suspension visibility semantics that Search can enforce. `ExternalResourceReference` objects are also not indexed yet because #44 currently does not expose them through a durable, listable canonical ResourceService; Search does not crawl sync responses or adapter state to invent such an index.

See `docs/SEARCH_CONNECTORS.md` for the focused integration contract.

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

Later domains are added only after their owning canonical APIs and privacy contracts are stable. Search must not invent a second schema authority for Memory, Knowledge, Nodes/Workers, Conversations, Notifications, Templates, Repository/Git, Verification, Organizations or other future domains.

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

Known Project, Workspace, Task, Run, Artifact, Result, Plan, Step, Model, Model-Provider and Approval results can navigate to their existing canonical UI route. Other indexed types retain their canonical API reference even where the owning domain does not yet provide a dedicated frontend detail route. The Search frontend does not invent client-only identities or routes.

## Synchronization semantics

The provider boundary supports:

- `upsert(document)` for write-through or event-driven refresh;
- `delete(resource_type, resource_id)` for tombstone/deletion propagation;
- `rebuild(documents)` to replace the entire derived index from canonical sources.

The baseline local provider replaces documents by `(resource_type, resource_id)`, so updates do not create duplicate identities. `rebuild(...)` atomically replaces its in-memory derived state.

For the current correctness-first Control Plane path, the index is rebuilt from canonical sources before each query. Updates and deletions therefore cannot remain silently stale even without a durable index or event bus. Rebuild also removes provider-only stale documents that no longer exist in canonical state.

Later durable providers may replace the per-query rebuild with write-through/event-driven synchronization plus revision/checkpoint metadata, batching and missed-event recovery. That synchronization metadata remains provider provenance and never becomes canonical resource state. A full canonical rebuild remains the recovery authority for all indexed resource types.

A rebuild tolerates absent optional domains: for example, Search remains functional when no Model Registry, Registry/Marketplace connection or optional registered ResourceService is configured.

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

For registered actor-filtered domains, an internal rebuild enumerator may expose more candidate resources than one caller could list directly. This is safe only because the raw SearchProvider is not northbound and the Control Plane re-authorizes every candidate using its canonical owner/Project scope before it can influence visible totals or results.

## Failure and degraded behavior

- A SearchProvider outage is returned through the canonical error contract, including retryable `503 unavailable` where appropriate.
- Unsupported optional query modes return `unsupported_capability` rather than silently changing semantics.
- The CLI preserves those canonical errors.
- The frontend distinguishes optional-mode degradation from general request/provider failures.
- The baseline has no dependency on Registry connectivity, embeddings, vector databases or paid search services.
- A failed rebuild does not make stale provider state authoritative; the request fails rather than silently serving a potentially incorrect authorization-sensitive snapshot as fresh canonical Search.

## Remaining #45 integrations

The secure Search foundation, Task/reference/Event indexing, progressive registration bridge, Model/Capability inventory support, Automation/Approval/File/Plugin/Usage/Evaluation/Connector discovery, Task-management filtering and CLI/frontend clients now establish one canonical discovery path across the currently stable domains.

Remaining progressive work is intentionally gated on the owning canonical APIs and privacy contracts:

- scoped Memory and Knowledge after privacy-aware canonical content enumeration exists (#251/#13);
- Nodes/Workers after #14 stabilizes its northbound registry/resource contract;
- Conversations/Messages with retention/deletion propagation (#72);
- Notifications where useful after the currently reopened #75 is stable;
- Templates (#78);
- Repository/Git and durable external-resource references (#82/#44);
- Organizations/Memberships with membership-removal/suspension isolation (#87);
- Organization-scoped Connector Connections after #87 visibility semantics are available;
- durable/event-driven indexing, batching and stale-index checkpoints for larger deployments as an optimization over the correctness-first rebuild path;
- optional semantic/hybrid provider adapters without making them baseline requirements.

External Registry/Marketplace search remains a separate optional distribution concern and is not required for local platform Search.
