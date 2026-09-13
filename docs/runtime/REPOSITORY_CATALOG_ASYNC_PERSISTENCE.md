# Async repository binding catalog persistence

Issue #892 requires runtime-critical async services to depend on awaitable, backend-neutral
persistence semantics rather than executing synchronous `sqlite3` work on the event-loop thread.
The repository integration keeps its existing durable SQLite catalog for local/single-node
persistence, but runtime callers now use the `RepositoryBindingCatalog` async contract.

## Runtime contract

`RepositoryBindingCatalog` expresses repository-binding application operations only:

- save one canonical binding record;
- get one binding by canonical repository ID;
- list all bindings or those for one canonical Connection;
- delete one binding.

The contract intentionally exposes no SQL, connection or transaction primitives. A future
Postgres implementation can implement the same awaitable methods without changing
`RepositoryManagementService` or connector restore callers. `InMemoryRepositoryBindingCatalog`
provides deterministic non-durable contract coverage.

## SQLite adapter

`AsyncSqliteRepositoryBindingCatalog` adapts the existing `SqliteRepositoryBindingCatalog` at the
runtime composition boundary. The synchronous catalog remains available to bootstrap and other
explicitly synchronous setup paths.

Runtime SQLite operations follow these rules:

1. blocking catalog calls execute through `asyncio.to_thread()` rather than on the event loop;
2. a per-adapter semaphore bounds submitted worker operations;
3. durable writes acquire a write lock before consuming shared worker capacity, so queued writers
   do not occupy read slots;
4. the wrapped SQLite catalog creates, uses and closes each connection inside the worker operation,
   so live `sqlite3.Connection` objects never cross threads;
5. cancellation is deferred until the worker operation has reached its transaction boundary, even
   when the coroutine is cancelled repeatedly;
6. SQLite `busy`/`locked` failures map to canonical retryable `TRANSIENT_FAILURE` errors, while
   existing domain errors retain their original semantics.

The adapter does not introduce a shared state-owning database layer or an ORM. Repository routing
metadata remains owned by the `repositories` package.

## Runtime call sites

`RepositoryManagementService` adapts a supplied synchronous SQLite catalog once in its constructor
and awaits catalog persistence for attach, detach and Connection cleanup. Service-owned catalog
mutations are serialized. An attached binding is published into the in-memory registry only after
its durable save succeeds, so an awaitable SQLite write cannot expose uncommitted routing state.
Detach hides the route before awaiting durable deletion and restores it if deletion fails. If an
unexpected registry-registration failure happens after persistence, the previous catalog record is
restored (or the new record is removed) before the error is propagated.

Caller cancellation is deferred across the whole logical management mutation, not only the SQLite
worker call. If a save commits while the caller is cancelled, the registry publish still completes
before `CancelledError` is returned. The same boundary lets detach finish its durable delete or its
rollback before cancellation becomes observable, preventing durable and in-memory routing state
from diverging because of cancellation timing.

`restore_connector_repositories()` likewise uses the async catalog boundary for durable list/delete
operations while it awaits canonical Connector state. Missing Connector Connections still remove
stale repository routing records, but the SQLite cleanup no longer blocks the event loop.

`restore_managed_local_repositories()` remains intentionally synchronous because it is an explicit
single-node startup/bootstrap function. Constructor-time SQLite schema initialization likewise
remains synchronous setup work and is outside the runtime awaitable path targeted by #892.

## Regression coverage

The integration suite verifies:

- in-memory/SQLite contract parity for save/get/list/delete semantics;
- event-loop heartbeat responsiveness during an intentionally slow SQLite catalog operation;
- SQLite connection creation on worker threads rather than the event-loop thread;
- bounded concurrent worker operations;
- repeated cancellation that does not release the write boundary before persistence settles;
- restart-visible persistence after a cancelled caller;
- service-level cancellation waits for durable save plus registry publication;
- canonical retryable mapping for SQLite busy/locked failures;
- failed attach persistence never publishes an uncommitted repository binding.
