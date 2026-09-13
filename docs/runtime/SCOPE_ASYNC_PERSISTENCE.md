# Project/Workspace Scope async persistence

Issue #892 requires runtime-critical async code to avoid inline synchronous SQLite work while retaining SQLite as a first-class single-node backend.

## Runtime boundary

The canonical synchronous `ScopeStore` / `SqliteScopeStore` API remains available for construction, restore validation, offline tooling, benchmark setup, direct persistence tests, and other explicitly synchronous compatibility paths.

Async production paths use the backend-neutral `AsyncScopeStore` contract instead. It covers canonical Project/Workspace identity reads and mutations:

- Project create, complete-snapshot import, compensation, get and list;
- Workspace identity create, get and list.

`AsyncScopeStoreAdapter` bridges an existing synchronous store. A future backend such as PostgreSQL can implement `AsyncScopeStore` directly without changing Control Plane, Template or portability runtime consumers.

The base Control Plane exposes both seams deliberately:

- `scopes` is the synchronous setup/offline compatibility view;
- `runtime_scopes` is the awaitable runtime view.

## Executor and connection ownership

`ScopePersistenceOffload` owns a dedicated bounded `ThreadPoolExecutor`. It never uses asyncio's process-wide default executor, so queued Scope persistence cannot consume unrelated runtime worker capacity.

SQLite connections created by runtime mutations are worker-owned because the complete synchronous `ScopeStore` operation executes inside that executor. SQLite schema initialization and initial state loading remain synchronous construction-time operations and are not part of the request-time boundary.

## Shared serialization

Scope persistence combines durable SQLite state with canonical in-memory Project/Workspace identity state. Reads therefore serialize with writes, not only with other reads or writes independently.

Serialization ownership follows the backing `ScopeStore`. Independently constructed `AsyncScopeStoreAdapter` instances for the same store resolve through one weak-keyed shared `ScopePersistenceOffload` and one Scope serialization lock. This prevents an async reader from observing a partial in-memory/durable transition while another adapter is mutating the same canonical store.

The weak registry does not extend the backing store lifetime. When the store and its adapters are unreachable, the shared offload can be collected instead of becoming a process-lifetime executor leak.

## Cancellation and errors

Caller cancellation is shielded until the complete synchronous Scope operation finishes. A cancelled Project or Workspace write therefore cannot report cancellation while its SQLite transaction or corresponding in-memory update is still unresolved. If the worker fails while cancellation is pending, the worker failure wins so the authoritative persistence error is not hidden.

SQLite `busy` and `locked` operational failures map to retryable `TRANSIENT_FAILURE`; other SQLite failures map to `BACKEND_ERROR`. Existing domain failures such as `NOT_FOUND`, `CONFLICT` and invalid canonical identifiers remain unchanged.

## Runtime consumers

The Scope cohort routes request-time Project/Workspace CRUD, Project validation for Task creation, Project portability import/rollback, Project Template instantiation/compensation/export and other audited async Scope consumers through `runtime_scopes` or an equivalent injected `AsyncScopeStore`.

Restore/integrity code and startup composition remain synchronous by design when they execute outside an event loop and are not request-time runtime paths.

## Regression coverage

The #892 Scope persistence tests cover:

- event-loop responsiveness and worker-owned SQLite connections;
- bounded dedicated executor use and isolation from asyncio's default executor;
- shared serialization across independently constructed adapters;
- weak backing-store ownership;
- async reads waiting behind an in-flight mutation;
- cancellation waiting for a durable Project write before surfacing;
- retryable SQLite busy/locked mapping;
- Project/Workspace async parity and restart durability.
