# Learning async persistence boundary

Issue #892 requires governed Learning runtime paths to remain responsive while the single-node deployment uses the synchronous stdlib `sqlite3` driver.

## Runtime boundary

Canonical single-node Learning composition uses `RuntimeGovernedLearningService`. Its persistence-facing async methods and `LearningRuntimeAdapter` route synchronous `LearningRepository` work through one shared `LearningPersistenceOffload` per backing repository object. The separate post-promotion evaluation recorder uses its own shared offload so derived evaluation bookkeeping cannot consume the primary Learning repository boundary.

The synchronous `LearningService` and repository methods remain compatibility seams for construction, tests, migrations and explicitly synchronous/offline callers. Async production callers must use the runtime service/adapter surfaces instead of invoking SQLite-backed Learning methods inline on the event loop.

## Concurrency and connection ownership

`LearningPersistenceOffload` owns a dedicated `ThreadPoolExecutor`; it does not use asyncio's process-wide default executor. `max_pending` is enforced before submission, and exhausted capacity fails with retryable `TRANSIENT_FAILURE` instead of allowing an unbounded queue.

Operations sharing one Learning repository object resolve to the same offload through `SharedPersistenceOffloadRegistry`. A store lock serializes repository calls inside the owned worker pool. `SQLiteLearningRepository` opens SQLite connections inside those repository calls, so runtime connections are created and used on the persistence worker rather than on the asyncio event-loop thread. Constructor-time schema initialization remains synchronous setup work.

Post-promotion persistence resolves through a distinct `learning-post-promotion-persistence` boundary because it owns a separate backing store.

## Cancellation and failure semantics

Once submitted persistence has started, caller cancellation is shielded until that operation settles. A persistence failure wins over a pending cancellation; otherwise cancellation is surfaced only after the started operation has completed. This prevents callers from observing cancellation while a write is still ambiguously in flight.

Existing `ContractError` domain semantics are preserved. Raw SQLite `busy`/`locked` operational errors are normalized to retryable `TRANSIENT_FAILURE`; other raw SQLite failures become `BACKEND_ERROR`.

## Backend neutrality

Runtime callers depend on awaitable Learning service/adapter operations rather than on SQLite primitives. The synchronous repository remains behind that boundary, so an in-memory or future Postgres-backed implementation can preserve the same caller-facing async workflow without changing Control Plane/source callers.

## Regression coverage

`tests/integration/evaluation/test_learning_async_persistence.py` verifies:

- event-loop heartbeat progress while a SQLite Learning read is deliberately blocked on the worker;
- worker-thread execution and the Learning-owned executor name;
- bounded queue rejection;
- cancellation after started persistence settles;
- SQLite busy/locked and generic backend error mapping;
- shared primary-repository serialization and separate post-promotion persistence;
- InMemory/SQLite read parity through `LearningRuntimeAdapter`.

Existing governed-Learning repository/service suites continue to cover append-only revisions, conflicts, restart durability, promotion semantics and broader domain behavior.
