# Async Knowledge SQLite persistence

Issue #892 requires awaitable runtime services to avoid synchronous SQLite work on the asyncio event-loop thread. The local Knowledge provider remains SQLite-backed while the existing `KnowledgeProvider` application contract remains backend-neutral and awaitable.

## Runtime boundary

`LocalKnowledgeProvider` continues to expose canonical Knowledge source, document, index and search semantics. Runtime SQLite operations are executed in Knowledge-owned workers, including source registration/inspection, ingestion, index status, keyword search, reindex/remove, lifecycle metadata updates and discovery snapshots.

Constructor-time schema initialization remains synchronous setup work. No SQL, connection, transaction, thread or executor primitive crosses the provider contract.

## Worker ownership and concurrency

Each provider instance owns a dedicated `ThreadPoolExecutor(max_workers=max_concurrency)`. A Knowledge backlog therefore queues inside the Knowledge provider instead of consuming asyncio's process-wide default executor. Reads may use the configured worker concurrency while durable writes are serialized by a provider-owned thread lock.

Every runtime SQLite connection is opened, used and closed inside the worker operation. Live `sqlite3.Connection` objects never cross the worker/event-loop boundary or move between worker threads. The executor and write lock are independent of any one asyncio loop, so the existing local provider remains reusable across separate `asyncio.run(...)` lifetimes.

Logical reindex operations additionally use a loop-independent per-source serializer. Reindexes for different canonical sources may proceed concurrently, but two reindexes for the same source cannot interleave their `INDEXING`, ingestion and terminal-state transitions.

## Transaction boundaries

Application-semantic mutations remain complete worker-side transactions rather than collections of independently offloaded SQL statements:

- source registration inserts the source and canonical index together;
- ingestion reads the current source revision, inserts the document, and transitions source/index state to `READY` in one connection transaction;
- removal validates the source, marks it `REMOVED`, and deletes its canonical index in one serialized transaction;
- lifecycle metadata update reads and writes the canonical source under one serialized worker boundary.

Reindex is intentionally a logical multi-step lifecycle: persist `INDEXING`, ingest the new revision, then end in `READY` or, for lifecycle-capable providers after a backend or transient persistence failure, durable `FAILED`. Caller cancellation is shielded across that whole logical mutation so it cannot become observable while the source is stranded between those states. If the underlying operation fails while cancellation is pending, the persistence failure remains authoritative.

The lifecycle FAILED checkpoint is conditional on the source still matching the requested revision and `INDEXING` state. A stale failure therefore cannot overwrite a newer successful reindex. The checkpoint is best-effort and never replaces the original `BACKEND_ERROR` or retryable `TRANSIENT_FAILURE` returned to the caller.

Compatibility `index(...)` is likewise shielded across source creation plus ingestion, preventing cancellation from exposing a newly registered but unintentionally un-ingested source merely because SQLite is now truly asynchronous.

## Error semantics

Existing domain errors retain their canonical codes. SQLite `busy`/`locked` operational failures become retryable `TRANSIENT_FAILURE`; other raw SQLite runtime failures become `BACKEND_ERROR`. Uniqueness conflicts remain canonical `CONFLICT` errors. Lifecycle reindex preserves those original errors while ensuring an already-started revision reaches a terminal persisted state whenever the metadata store remains writable.

## Backend neutrality and regression coverage

Service callers continue to depend only on `KnowledgeProvider`, so a future Postgres implementation can satisfy the same contract without changing callers. Existing Knowledge lifecycle/restart/search tests remain applicable.

Issue #892 integration coverage additionally verifies event-loop responsiveness, worker-thread connection ownership, bounded worker concurrency, isolation from asyncio's shared default executor, repeated cancellation through reindex completion, same-source concurrent reindex serialization, retryable busy mapping with terminal FAILED state, rollback of a partially attempted ingest, and restart-visible search/index semantics.
