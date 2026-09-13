# Async Notification preference persistence

Issue #892 requires async runtime services to avoid executing synchronous `sqlite3` work on the asyncio event-loop thread. Notification preferences therefore expose an awaitable application-facing persistence contract while SQLite remains the local reference backend.

## Runtime contract

`AsyncNotificationPreferenceRepository` is the backend-neutral persistence seam used by async application/runtime services:

- `get(recipient)` returns the persisted preference or the canonical default for that recipient;
- `save(preference)` persists and returns the canonical preference.

Both operations are awaitable. `NotificationService` and the Notification Control Plane therefore do not know whether the implementation is in-memory, SQLite-backed, or a future remote database adapter.

The pre-existing synchronous `NotificationPreferenceRepository` seam remains available for legacy/non-runtime adapters. The existing synchronous SQLite preference implementation is used internally for schema/bootstrap setup, while the public runtime `SqliteNotificationPreferenceRepository` exported by the Notifications package is the awaitable adapter used by service composition.

## SQLite runtime boundary

Constructor-time directory and schema initialization remain synchronous setup work. Runtime preference reads and writes are executed through the Notification-owned offload boundary.

Each operation creates, uses and closes its SQLite connection inside the worker thread. No live `sqlite3.Connection` or cursor crosses back to the event-loop thread.

Preference serialization/deserialization remains part of the persistence operation, so callers receive domain objects rather than driver-specific rows or payloads.

## Concurrency, executor isolation and event-loop lifetime

Each Notification SQLite adapter owns a dedicated `ThreadPoolExecutor` bounded by `max_concurrency`. Notification backlog therefore queues inside the adapter-owned executor instead of occupying asyncio's process-wide default executor. Unrelated `asyncio.to_thread(...)` work can continue even when Notification persistence has more queued operations than its configured concurrency limit.

Reads may execute concurrently up to the configured worker bound. Durable writes additionally acquire the adapter's thread-based mutation gate, keeping writes serialized without introducing an asyncio-loop-bound synchronization primitive.

Both the executor and the write gate are independent of an asyncio event-loop lifetime, so one adapter instance remains reusable across separate `asyncio.run(...)` lifetimes, matching existing test and embedding behavior.

## Cancellation and transaction boundary

Caller cancellation does not release the persistence boundary while the synchronous SQLite worker may still be committing or rolling back. Cancellation is deferred until the worker operation has settled, so an awaited mutation never returns while its durable outcome is still unknown.

Each preference save is one worker-side SQLite transaction using one connection. Reads similarly remain complete worker operations.

## Error semantics

Raw SQLite `busy` or `locked` operational failures map to canonical retryable `TRANSIENT_FAILURE`. Other raw SQLite runtime failures map to `BACKEND_ERROR`.

Domain and contract errors remain backend-neutral and are not translated into SQLite-specific exceptions at the service boundary.

## Service and Control Plane integration

Preference I/O is awaited throughout Notification runtime paths, including:

- candidate filtering in `NotificationService.create` and `create_once`;
- in-app visibility checks and unread counts;
- preference reads and updates exposed through the Control Plane;
- reminder evaluation and runtime preference resources;
- authorization-aware preference resources;
- Notification Search result visibility checks.

No async handler needs to know whether preference persistence is SQLite-backed.

## Conformance and regressions

The in-memory preference repository implements the same `AsyncNotificationPreferenceRepository` contract as SQLite. #892 regression coverage verifies:

- event-loop heartbeat responsiveness during intentionally slow SQLite preference work;
- worker-thread SQLite connection ownership;
- bounded concurrent operations;
- isolation of queued Notification work from asyncio's process-wide default executor;
- reuse across multiple event-loop lifetimes;
- cancellation deferred to the persistence boundary;
- retryable busy/locked mapping;
- restart durability and backward-compatible decoding;
- equivalent default/save/get semantics between the in-memory and SQLite implementations.
