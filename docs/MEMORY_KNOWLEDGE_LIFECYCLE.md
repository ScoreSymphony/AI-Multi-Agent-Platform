# Memory and Knowledge content lifecycle

Issue #251 defines the canonical northbound content-management contract for Memory and Knowledge. It builds on the replaceable data-provider boundaries from ADR 0002 and the lifecycle decisions in ADR 0007.

## Boundary

Memory and Knowledge are independent data concerns. Neither is a synonym for Files, Workspace state, Chat history, Task history, Run history or Event history.

Chat messages, Events and other canonical history must never become Memory implicitly. A durable Memory entry is either created explicitly or promoted explicitly from an existing canonical short-term Memory entry.

Knowledge is source-backed. The canonical identity is the Knowledge source and its source revision; provider-private index IDs, vector IDs or backend object keys are not canonical platform identities.

## Memory

### Scopes

The canonical Memory scopes are:

- `short_term`
- `task`
- `agent`
- `workspace` — the Project/Workspace scope established by ADR 0002
- `user`
- `historical`
- `organization`

There is intentionally no second `project` enum value. Project-scoped Memory uses `workspace` with the canonical Project ID as `scope_id`.

### Origin

Every Memory entry exposes one canonical origin:

- `user-authored`
- `agent-derived`
- `imported`

Origin is explicit metadata, not a provider-private convention. It is immutable for the lifetime of an entry. A change that would reclassify origin creates a different canonical entry instead of rewriting history.

### Provenance and supersession

Durable Memory preserves provenance. Explicit promotion from short-term Memory appends a `memory` source reference to the promoted source entry.

Updates use supersession rather than in-place content replacement. The replacement receives a new canonical Memory ID and points to the previous entry through `supersedes_memory_id`; the provider links the previous entry to its replacement.

### Expiry and deletion

Expiry and deletion are distinct lifecycle operations.

`memory.expire` is exact and scope-bound. The command requires `scope` and `scope_id` and delegates to the provider's exact `expire_entry(memory_id, query, context)` contract. The provider verifies the canonical scope before inspecting or tombstoning the entry and rejects entries that have no expiration timestamp or are not yet due.

`memory.delete` is an explicit deletion request for a currently visible canonical entry.

## Knowledge

### Canonical source metadata

A Knowledge source records at least:

- canonical source ID
- owner and optional Project scope
- creator
- title
- revision
- lifecycle/index status
- timestamps
- content checksum where available
- metadata

Source inspection is available through provider-neutral `get_source` and `list_sources` operations. Providers predating #251 may explicitly return `unsupported_capability`; callers must not reach into private provider methods as a workaround.

### Metadata update

`knowledge.update` changes canonical source metadata such as title and metadata without changing source identity or source revision. Content changes belong to ingestion/re-indexing rather than metadata update.

### Ingestion, re-indexing and degraded providers

`knowledge.ingest` attaches source-backed content to a registered source. `knowledge.reindex` records an explicit new source revision and refreshes retrieval state.

Provider-specific indexing failures remain distinguishable from canonical source identity. The reference provider transitions a re-index operation that started successfully but then fails in the ingestion/index backend to the explicit `FAILED` status for both the canonical source metadata and its index status. Source ID, title, ownership, scope and metadata remain intact; the attempted revision remains inspectable. The original backend error is still returned to the caller.

If the metadata store itself is unavailable, the provider does not mask the original backend failure with a secondary failure while attempting to record `FAILED`.

### Detach and delete

The reference provider represents source removal as a tombstone. Active retrieval state is removed while canonical source metadata and historical provenance/citation relationships remain intact.

`knowledge.detach` and `knowledge.delete` therefore expose an explicit removed state instead of silently erasing source identity required by historical citations.

## Control Plane

Canonical read resources:

- `GET /api/v1/memory`
- `GET /api/v1/memory/{memory_id}`
- `GET /api/v1/knowledge`
- `GET /api/v1/knowledge/{knowledge_source_id}`
- `GET /api/v1/knowledge-results?q=...`

Memory listing is scope-bound. Outside a canonical user context, callers must provide explicit `scope` and `scope_id`. Project/Workspace Memory uses the Project ID as the `workspace` scope ID.

Knowledge retrieval results are query-scoped projections. They contain canonical source/revision/citation information and do not create a second durable result identity or expose provider-private index identity. Because they only exist for an explicit retrieval query, `knowledge-results` is not indexed into the platform's global registered-resource Search index.

Canonical mutations use the existing idempotent Control Plane command boundary:

- `POST /api/v1/commands/memory.create`
- `POST /api/v1/commands/memory.promote`
- `POST /api/v1/commands/memory.update`
- `POST /api/v1/commands/memory.expire`
- `POST /api/v1/commands/memory.delete`
- `POST /api/v1/commands/knowledge.register`
- `POST /api/v1/commands/knowledge.update`
- `POST /api/v1/commands/knowledge.ingest`
- `POST /api/v1/commands/knowledge.reindex`
- `POST /api/v1/commands/knowledge.detach`
- `POST /api/v1/commands/knowledge.delete`

Mutating command requests require the normal `Idempotency-Key` contract.

## Authorization and non-disclosure

Authorization must run before provider lookup whenever an operation could reveal existence, count, source metadata, snippets or retrieval results.

A caller that is forbidden to inspect a source must not be able to distinguish an existing source from a missing source through a provider lookup side channel. Collection operations may omit inaccessible scopes rather than exposing their existence or counts.

Authorization policies receive canonical Memory origin/scope information and exact expiry scope information before the underlying provider mutation runs.

## Replaceability

The Control Plane and CLI depend on the refined Memory/Knowledge provider contracts, not SQLite tables, local filesystem paths, vector-store IDs or provider-private helper methods.

A provider that does not implement an optional #251 management capability fails explicitly with the canonical unsupported-capability error. Silent fallback to provider-private APIs is not allowed.

## CLI

The API-first CLI exposes dedicated top-level domains instead of using the generic extension-command inspector as a mutation executor.

Memory commands:

- `platform memory list`
- `platform memory show MEMORY_ID`
- `platform memory create SCOPE_ID --scope ... --origin ... --value-json ...`
- `platform memory promote MEMORY_ID --scope ... --scope-id ...`
- `platform memory update MEMORY_ID ...`
- `platform memory expire MEMORY_ID --scope ... --scope-id ...`
- `platform memory delete MEMORY_ID`

Knowledge commands:

- `platform knowledge list`
- `platform knowledge show SOURCE_ID`
- `platform knowledge search QUERY`
- `platform knowledge register TARGET_REF --title ...`
- `platform knowledge update SOURCE_ID ...`
- `platform knowledge ingest SOURCE_ID --content ... --location ...`
- `platform knowledge reindex SOURCE_ID --revision ... --content ... --location ...`
- `platform knowledge detach SOURCE_ID`
- `platform knowledge delete SOURCE_ID`

Mutations call only the canonical `/api/v1/commands/...` routes and forward explicit idempotency keys. `memory expire`, `memory delete`, `knowledge detach` and `knowledge delete` use the CLI's existing explicit confirmation boundary (`--yes` for non-interactive execution).
