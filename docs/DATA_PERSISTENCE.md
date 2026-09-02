# Persistence, Files, Scoped Memory and Knowledge

This document defines the data boundaries introduced for issue #13. The architecture deliberately separates canonical platform state, durable files/artifacts, memory and source-backed knowledge. A concrete database, object store, vector engine or cloud product is an adapter choice, not a canonical platform concept.

## Responsibility split

| Concern | Authoritative responsibility | Baseline implementation | Must not become |
|---|---|---|---|
| Canonical state | Task/Run/Event kernel repositories | Existing kernel repository + SQLite reference repository | Memory or search index |
| Files / durable objects | `data.FileProvider` | Local filesystem bytes + SQLite metadata | Raw path as canonical identity |
| Scoped memory | `data.MemoryProvider` | SQLite | Generic persistence bucket or task history |
| Knowledge / retrieval | `data.KnowledgeProvider` | SQLite source registry + deterministic keyword retrieval | Canonical source of task/run truth |

Canonical task/run/event history remains owned by the kernel. Memory may summarize or derive from that history, but mutable memory entries cannot replace the canonical event/state repositories.

## Canonical identifiers

The platform owns stable identifiers and never exposes backend primary keys, object keys, filesystem paths or vector IDs as canonical identity.

- file: `file_<uuid>`
- artifact: existing canonical `artifact_<uuid>`
- memory entry: `memory_<uuid>`
- knowledge source: `knowledge_source_<uuid>`
- knowledge document: `knowledge_document_<uuid>`
- knowledge index/reference: `knowledge_index_<uuid>`

Provider-private identifiers may be retained only as adapter metadata when a future adapter needs them.

## Provider layering

Issue #5 established coarse platform-wide provider seams. The `ai_multi_agent_platform.data` package refines the `FileProvider`, `MemoryProvider` and `KnowledgeProvider` contracts without changing the shared coarse interfaces. The refined contracts subclass the core interfaces, so a data provider is still usable wherever the core provider type is expected.

`DataProviderSet` is the dependency-injection hook for a future Control Plane. The Control Plane depends on provider contracts, never `LocalFileProvider`, `LocalMemoryProvider` or `LocalKnowledgeProvider` directly.

## Six-scope memory model

| Scope | Scope ID | Baseline persistence | Default lifetime | Access/isolation hook | Promotion / provenance |
|---|---|---|---|---|---|
| Short-Term Context | session/execution reference | Optional; local reference persists with mandatory expiry | max 24 hours | actor/task/run context | may be copied into another explicit scope; no silent promotion |
| Task Memory | canonical `task_…` | durable | task lifetime | task/project authorization | provenance may point to task/run/tool/model/event evidence |
| Agent Memory | canonical `agent_…` | durable | durable unless policy overrides | agent/revision/team policy in #33/#15 | supersession rather than destructive history rewrite |
| Project / Workspace Memory | canonical `project_…` | durable | project lifetime | hard project scope check + future #15 policy | inherited by tasks only through explicit policy |
| User Memory | user principal scope ID | durable | user lifetime | owner separation hook + future #15 policy | cross-project use only when policy explicitly permits |
| Historical Memory | explicit history scope reference | durable | durable/audit-derived policy | project/user policy | provenance is mandatory and must point back to canonical evidence |

Every memory entry records: canonical scope and scope ID, owner, creator, timestamp, retention/expiry, provenance, optional supersession links, classification hook and provider-neutral metadata. Embedding/vector metadata is not required.

### Memory invariants

1. Memory is not the source of truth for Task, Run or Event history.
2. Historical memory requires provenance.
3. Short-term memory requires expiry and cannot exceed 24 hours in the canonical reference model.
4. Task, Agent and Workspace scopes use platform-owned canonical IDs.
5. User memory is checked against operation-owner context when that identity is available.
6. Workspace memory is project-isolated before future authorization policy is evaluated.
7. Supersession creates a new memory entry and links old/new identities; it does not rewrite canonical history.
8. Expiry and deletion are lifecycle operations, not backend-row identity changes.

## File contract and integrity

The refined file contract supports:

- create/upload;
- read/download;
- streaming;
- metadata lookup;
- project-scoped listing;
- tombstone/delete;
- SHA-256 integrity verification;
- canonical Artifact linking;
- orphan detection.

The local provider uses a two-step write:

1. insert canonical metadata in `pending` state;
2. write a temporary object;
3. atomically rename the object into place;
4. mark metadata `ready`.

A failed object write is cleaned up and metadata is tombstoned. Reads validate the stored SHA-256 checksum and map missing/corrupt objects to canonical `ContractError` categories. The canonical API never returns the local filesystem path.

Orphan detection distinguishes:

- metadata that claims a ready object but whose bytes are missing;
- local canonical-looking objects that have no metadata record.

Cleanup policy is deliberately not automatic in this issue.

## Memory contract

The refined memory contract supports:

- write by explicit `MemoryEntry`;
- retrieve by canonical memory ID;
- query by scope/metadata boundary;
- provider-supported search;
- supersession;
- delete;
- expiry;
- provenance/source linkage.

The local reference provider implements keyword search over serialized values and has no embedding dependency.

## Knowledge contract

The refined knowledge contract supports:

- source registration;
- ingest/index;
- index status;
- source-backed search;
- citations/location metadata;
- reindex/update;
- removal;
- source revision tracking.

The local reference provider implements deterministic keyword retrieval only. Semantic and hybrid search are contract-level capability choices; unsupported modes fail with `unsupported_capability` rather than forcing a vector database into the architecture.

A retrieval hit carries canonical source/document IDs, source revision, location and a `SourceRef` citation. Search indexes are derived data and are never authoritative platform state.

## Consistency and transaction boundaries

The reference providers use SQLite transactions for metadata transitions and supersession/reindex operations. File bytes and metadata cannot share one database transaction, so the file provider uses explicit `pending -> ready/tombstoned` state plus orphan detection.

Rules:

- canonical IDs are allocated before backend writes;
- duplicate canonical IDs map to `conflict`;
- missing objects/references map to `not_found`;
- checksum corruption maps to `contract_violation`;
- project mismatch maps to `forbidden`;
- provider/backend failures are translated to canonical contract errors;
- multi-provider workflows must not pretend to have an atomic distributed transaction;
- future orchestration should use compensating cleanup/reconciliation when a workflow spans providers.

## Retention and deletion hooks

This issue models policy hooks rather than full governance:

- file records can be tombstoned;
- temporary memory has explicit expiry;
- task/project/user memory has explicit retention classes;
- user/project deletion can enumerate and delete scoped records through provider contracts;
- knowledge replacement uses source revisions and reindexing;
- historical memory remains mutable/supersedable derived data, while canonical events remain the immutable reconstruction source.

Legal/audit retention policy enforcement remains outside this issue.

## Security/access context

`DataAccessContext` preserves:

- actor/service reference;
- correlation/causation via `OperationContext`;
- project/workspace;
- optional task;
- optional run;
- optional agent;
- classification hook;
- audit metadata.

The local provider enforces project isolation and user-owner separation where enough identity context exists. Issue #15 remains responsible for final authorization/approval decisions. Data providers must preserve this context so #15 can enforce policy without redesigning storage contracts.

## Baseline configuration

See `docs/examples/data.local.toml`. The reference stack requires only the Python standard library, SQLite and a writable local filesystem. It does not require paid APIs, cloud object storage or a vector database.

## Replacement strategy

A production deployment may replace any provider independently:

- `FileProvider` -> local filesystem, object store, NAS, content-addressed store;
- `MemoryProvider` -> relational, document, search-assisted or specialized memory backend;
- `KnowledgeProvider` -> keyword, semantic or hybrid retrieval stack.

Replacement must preserve canonical IDs and contract error/scope semantics. Backend keys stay private to the adapter.
