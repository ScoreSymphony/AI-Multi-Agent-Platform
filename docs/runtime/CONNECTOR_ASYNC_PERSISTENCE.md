# Async Connector SQLite persistence

Issue #892 requires runtime-critical async services to avoid synchronous `sqlite3` work on the asyncio event-loop thread. Connector persistence already exposes a backend-neutral, awaitable `ConnectorRepository`; the local SQLite implementation now preserves that contract while moving blocking runtime I/O to Connector-owned workers.

## Runtime boundary

The public `ai_multi_agent_platform.connectors.SqliteConnectorRepository` performs runtime source-definition, Connection, external-resource and sync-checkpoint persistence through a bounded worker executor. Constructor-time directory creation, schema inspection and schema migration remain synchronous setup work.

The existing `ConnectorRepository` contract is unchanged. Callers such as `ConnectorService` continue to depend on application semantics rather than SQLite or worker primitives, and a future Postgres implementation can satisfy the same contract without changing service callers.

## Worker ownership and concurrency

Each SQLite Connector repository instance owns a dedicated `ThreadPoolExecutor` with configurable `max_concurrency` (default `4`). Connector persistence therefore does not queue blocking work in asyncio's process-wide default executor.

Durable writes are serialized by a Connector-owned thread lock. Reads may execute concurrently up to the configured worker bound. Every runtime SQLite connection is created, used and closed inside one worker operation; live `sqlite3.Connection` and `sqlite3.Row` objects do not cross the worker/event-loop boundary.

The worker executor and write lock are not bound to one asyncio event loop, so the repository remains reusable across separate `asyncio.run(...)` lifetimes used by CLI, test and single-node composition paths.

## Transaction and cancellation semantics

Multi-step application mutations remain complete worker-side transaction boundaries. In particular:

- Connection revision validation plus upsert remains one serialized transaction;
- unused-Connection compensation verifies lifecycle state, sync history and durable resource references before deletion in one transaction;
- external-resource canonicalization plus persistence remains one transaction;
- authoritative external-resource replacement validates/canonicalizes the replacement set, deletes the old set and inserts the new set atomically;
- sync-checkpoint parent validation plus upsert remains one transaction.

Caller cancellation is shielded until the synchronous worker operation has committed or rolled back. Repeated caller cancellation does not release the repository boundary early. If the worker itself fails while cancellation is pending, that persistence failure remains authoritative rather than being hidden behind cancellation.

## Error semantics

Existing `ContractError` domain failures pass through unchanged. SQLite `busy`/`locked` operational failures map to canonical retryable `TRANSIENT_FAILURE`; other raw SQLite runtime failures map to `BACKEND_ERROR`.

## Regression coverage

Issue #892 Connector integration coverage verifies:

- event-loop heartbeat responsiveness during intentionally slow SQLite work;
- runtime connection creation on worker threads rather than the event-loop thread;
- bounded Connector worker concurrency;
- isolation from asyncio's shared default executor;
- repeated cancellation through a durable write boundary;
- retryable busy/locked mapping;
- rollback of a failed authoritative external-resource rebuild;
- restart durability; and
- parity between the in-memory and SQLite implementations for the shared `ConnectorRepository` contract.
