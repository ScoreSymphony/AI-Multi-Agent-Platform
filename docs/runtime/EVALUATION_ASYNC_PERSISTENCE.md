# Evaluation async persistence

Evaluation has three durable SQLite-backed stores in the single-node runtime:

- evaluation history (`SqliteEvaluationRepository`) for runs, results, aggregates and comparisons,
- immutable reproducibility manifests (`SqliteEvalManifestRepository`), and
- mutable/versioned suite assets (`SqliteEvaluationSuiteAssetRepository`).

All three stores use the same `evaluation.sqlite3` database in the single-node composition. Runtime access therefore shares one Evaluation-owned `EvaluationPersistenceOffload` instance instead of creating one executor and write lock per logical repository.

## Sync setup/offline API and awaitable runtime API

The existing synchronous repository contracts remain supported for setup, offline tooling, deterministic CI helpers and legacy callers. They are not removed or changed into async-only contracts.

Async request/runtime paths use backend-neutral awaitable facades from `evaluation.async_persistence`:

- `AsyncEvaluationHistoryRepository`,
- `AsyncEvalManifestRepository`, and
- `AsyncEvaluationSuiteAssetRepository`.

The provided adapters wrap the existing synchronous repositories. `EvaluationRunner`, the async `EvaluationService` methods, Control Plane handlers, target-aware execution and portability import/rollback use these awaitable seams.

## Dedicated bounded executor

`EvaluationPersistenceOffload` owns a dedicated `ThreadPoolExecutor` with bounded `max_concurrency` (default `4`). Evaluation SQLite backlog therefore queues inside the Evaluation-owned executor and does not consume asyncio's process-wide default executor.

Runtime SQLite connections are opened inside the worker operation because the synchronous repositories create their connection when the offloaded repository method executes. Startup initialization may still open setup connections synchronously before the async request path is exposed.

## Shared write serialization

One `EvaluationPersistenceOffload` instance is shared by History, Manifest and Suite Asset adapters that target the same SQLite file. The offload owns one process-local write lock. Every runtime write crosses that common barrier, including:

- run/result/aggregate/comparison persistence,
- immutable manifest persistence,
- suite creation, and
- suite deletion/rollback.

Reads may run concurrently up to the configured executor bound. SQLite WAL and the repository transactions remain responsible for database-level read/write behavior.

Suite deletion keeps its reference check and deletion in the existing synchronous repository transaction. Offloading wraps that complete operation as one serialized mutation, so a referenced suite still cannot be deleted between the dependency check and mutation within this process.

## Connection and transaction ownership

The worker invokes the existing synchronous repository method. That method owns its SQLite connection and transaction for the complete operation and closes the connection before returning. Connections are therefore not created on the event-loop thread and are not shared across worker threads or event-loop lifetimes.

Failed synchronous writes retain their existing SQLite context-manager rollback behavior. The async facade does not split a repository mutation into multiple worker operations.

## Cancellation semantics

Runtime persistence is a completion boundary. Caller cancellation is shielded until the submitted worker operation has completed. Repeated cancellation requests remain deferred while the worker is active.

After the worker completes:

- a successful operation re-propagates the caller's `CancelledError`, while
- a worker failure is raised instead of being hidden by the pending cancellation.

This prevents a caller from observing cancellation while a durable mutation is still in flight and preserves persistence failures as the authoritative result of a failed boundary.

## Busy/locked mapping

SQLite busy/locked failures are normalized by the async adapters to canonical `ContractError(ErrorCode.TRANSIENT_FAILURE, retryable=True)`. Other SQLite backend failures remain `BACKEND_ERROR`. Existing domain conflicts such as immutable-manifest conflicts, duplicate suites, checksum conflicts and referenced-suite deletion conflicts are preserved.

## Multi-loop behavior

The executor and write lock are thread-owned primitives rather than loop-owned asyncio primitives. Each call creates its `Future` on the currently running loop while submitting work to the shared executor. The same runtime facade can therefore be used across multiple sequential `asyncio.run(...)` lifetimes.

## Backend neutrality

The runtime service and runner depend on awaitable protocols, not directly on SQLite or `asyncio.to_thread(...)`. Alternative async-native backends can implement the same protocols without using the thread-pool adapters. SQLite-specific offloading remains confined to the adapter/composition layer.

## Startup reconciliation

`SqliteEvaluationRepository.reconcile_interrupted_runs()` is intentionally still synchronous in the single-node composition. It is an explicit restart/startup reconciliation step executed before the async Control Plane/request runtime is exposed, not request-path persistence. Keeping it synchronous preserves the clear startup boundary without consuming runtime executor capacity.
