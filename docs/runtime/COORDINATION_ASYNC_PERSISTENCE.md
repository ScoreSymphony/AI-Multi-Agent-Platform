# Coordination async persistence boundary

The canonical Plan/Step runtime avoids synchronous `sqlite3` work on the asyncio event-loop thread while SQLite remains the supported single-node backend.

## Runtime contract

`AsyncCoordinatorRepository` is the backend-neutral persistence contract used by async Coordination runtime code. It exposes application semantics for Plans, Step coordination records, fenced claims, active-plan discovery and Plan retirement; it does not expose SQL or SQLite connection objects.

The existing synchronous `CoordinatorRepository` remains available for setup, migrations, tests, benchmarking and explicit offline/compatibility callers. Production async coordinator methods resolve it through `runtime_coordinator_repository(...)` and never call its persistence methods inline.

A future native async backend such as Postgres can implement `AsyncCoordinatorRepository` directly without changing coordinator, Control Plane, Evaluation, Context or coding-dispatch callers.

## SQLite offload policy

`AsyncCoordinatorRepositoryAdapter` delegates a synchronous repository to one shared `CoordinationPersistenceOffload` per backing repository instance.

The offload boundary has these invariants:

- it owns a dedicated `ThreadPoolExecutor`; the asyncio default executor is not used;
- pending/running submissions are bounded before executor enqueue;
- all operations for one backing repository are serialized through one lock, including reads, writes, claims and Plan retirement;
- SQLite connections remain repository-local and are created by the worker thread that performs the operation;
- adapters wrapping the same repository share the same serialization/offload boundary;
- offload ownership is weak so adapters do not turn repository lifetime into a process-global leak.

## Cancellation and errors

Once persistence has started, caller cancellation does not abandon the worker. The adapter waits until the operation settles. If the worker fails while cancellation is pending, that persistence failure is surfaced instead of hiding it behind `CancelledError`.

SQLite busy/locked failures map to retryable `TRANSIENT_FAILURE`. Other SQLite failures map to `BACKEND_ERROR`. Existing domain `ContractError` values that do not wrap SQLite failures pass through unchanged.

## Ordering and durability

A completed awaited mutation is visible to later awaited reads through the same backing repository. The SQLite repository retains its existing transaction, optimistic revision and fenced-claim semantics; the async adapter does not invent a second transaction model.

Restart durability and superseded-Plan retirement continue to be owned by the canonical SQLite repository. The async boundary changes where blocking work executes, not what is persisted.

## Compatibility seams

The synchronous `DurablePlanStepCoordinator.projection()` remains intentionally available for tests, benchmarks and offline/setup tooling. Runtime async paths use `async_projection()`.

Benchmarking harnesses that deliberately measure SQLite or perform setup/checkpoint work may continue to use synchronous repository calls. They are not request-serving runtime paths and are audited separately from Control Plane and Agent execution.
