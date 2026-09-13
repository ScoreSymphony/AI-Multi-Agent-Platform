# Async Memory SQLite persistence

Issue #892 requires awaitable runtime services to avoid executing synchronous `sqlite3` work on the asyncio event-loop thread. The local Memory provider remains SQLite-backed, while its existing provider contract stays backend-neutral and awaitable.

## Runtime boundary

`LocalMemoryProvider` continues to implement the canonical `MemoryProvider` application semantics. Callers do not receive SQL, connection, transaction, thread, executor, or SQLite-specific primitives.

Constructor-time schema creation and lifecycle migration remain synchronous deployment/setup work. Runtime Memory operations are offloaded:

- write, get and scoped query;
- supersede and delete;
- bulk expiry and exact scoped expiry;
- discovery snapshot enumeration;
- the compatibility `put` / `get` surface through the same canonical methods.

Search remains an async application operation but performs no additional persistence I/O beyond the offloaded scoped query.

## Concurrency, executor isolation and connection ownership

Each Memory provider owns a dedicated `ThreadPoolExecutor` bounded by `max_concurrency`. Memory backlogs therefore remain queued inside the Memory-owned executor instead of occupying worker threads in asyncio's process-wide default executor. Unrelated `asyncio.to_thread(...)` work can continue even when Memory has more queued operations than its concurrency limit.

Reads may execute concurrently up to the configured bound. Durable writes are serialized by the Memory persistence boundary while the dedicated executor provides the global per-provider worker cap.

Every runtime SQLite connection is created, used and closed inside the Memory worker operation. No live `sqlite3.Connection` is handed back to the event-loop thread or shared between worker threads.

The worker executor and write serialization are not bound to an asyncio event loop, so a provider remains reusable across separate `asyncio.run(...)` lifetimes, matching existing reference-provider test and CLI usage.

## Transactions and cancellation

Multi-step durable mutations remain one serialized worker-side SQLite transaction. Supersede reads and validates the current entry, inserts the replacement and links the previous entry within one connection. Delete reads, validates and tombstones within one serialized boundary. Expiry reads/verifies/tombstones entries before leaving the same transaction boundary.

Caller cancellation does not release the persistence boundary while the synchronous worker may still be committing or rolling back. Repeated cancellation is deferred until the worker settles. If the worker itself fails while cancellation is pending, that persistence failure remains authoritative rather than being hidden by `CancelledError`.

## Error semantics

Provider/domain errors retain their existing canonical codes. SQLite `busy` and `locked` operational failures map to retryable `TRANSIENT_FAILURE`; other raw SQLite runtime failures map to `BACKEND_ERROR`. Existing uniqueness conflicts remain canonical `CONFLICT` errors.

## Backend neutrality

No caller depends on the offload implementation. The public async `MemoryProvider` contract remains suitable for the reusable in-memory conformance provider and for a future Postgres-backed provider without changing service callers.

The existing scoped-memory conformance suite continues to run against both the in-memory provider and `LocalMemoryProvider`. #892-specific integration coverage additionally verifies event-loop responsiveness, worker-thread connection ownership, bounded concurrency, shared-default-executor isolation, cancellation at the persistence boundary, busy mapping, rollback, concurrent delete semantics and restart durability.
