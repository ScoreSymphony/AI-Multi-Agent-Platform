# Security Approval async persistence

Issue #892 requires runtime-critical async code to avoid inline synchronous SQLite work while retaining SQLite as a first-class single-node backend.

## Runtime boundary

Security keeps the existing synchronous `ApprovalService`, `SqliteApprovalService`, and `SqliteAuthorizationAuditSink` surfaces for setup, offline tooling, compatibility, and direct persistence tests. Async runtime callers use backend-neutral awaitable contracts instead:

- `AsyncApprovalService` for approval resolution, inspection, creation, decision, and cancellation;
- `AsyncAuthorizationAuditSink` for durable authorization-audit appends.

`AuthorizationGate` owns these runtime facades. When only the existing synchronous stores are supplied, it automatically composes adapters for them, so callers of `decide()`, `enforce()`, `decide_approval()`, `cancel_approval()`, and `ensure_pending_approval_with_event()` do not depend on SQLite or on a thread-offload implementation.

Northbound Approval resource reads and Approval decision composition reuse the gate's awaitable Approval facade rather than calling the synchronous service directly.

## Executor and connection ownership

`SecurityPersistenceOffload` owns a bounded `ThreadPoolExecutor`. It does not use asyncio's process-wide default executor, so a Security persistence backlog cannot consume worker capacity needed by unrelated runtime work.

SQLite connections remain worker-owned because every synchronous repository operation, including connection creation, executes inside that executor. The offload object is independent of an event loop and can therefore be reused across sequential `asyncio.run(...)` lifetimes.

A default `AuthorizationGate` shares one Security offload between its Approval and authorization-audit adapters. Approval operations and audit appends target separate SQLite databases and therefore use separate serialization domains while sharing the same bounded worker pool.

## Approval serialization and expiration

All runtime Approval operations use the `approvals` serialization domain, including reads. This is intentional: `ApprovalService.get()` may transition an expired pending Approval to `EXPIRED`, and `all()`, `pending_for()`, `find_valid_for()`, and `valid_for()` may reach that path. Treating those reads as part of the same domain prevents expiration persistence from racing with request, decision, or cancellation mutations.

Authorization-audit writes use their own `audit` serialization domain. Audit storage is append-only and independent from Approval state.

## Cancellation and error semantics

Caller cancellation is shielded until the worker-side persistence boundary settles. Repeated cancellation therefore cannot report cancellation while a Security SQLite mutation is still unresolved. If the worker fails while cancellation is pending, the persistence failure wins over the pending cancellation so callers do not lose the authoritative storage failure.

SQLite `busy` and `locked` operational failures map to retryable `TRANSIENT_FAILURE`; other SQLite failures map to `BACKEND_ERROR`. Existing domain errors such as `NOT_FOUND`, `CONFLICT`, and `FORBIDDEN` are preserved.

`SqliteApprovalService` also restores its in-memory record state when a durable request/decision/cancellation/expiration write fails. The synchronous store therefore cannot report a mutation from memory that was not committed to SQLite.

## Backend neutrality

Async Security callers depend only on the awaitable protocols. A future PostgreSQL implementation can provide those protocols directly without changing `AuthorizationGate`, Approval Control Plane resources, or Approval decision command callers.

## Regression coverage

The #892 Security persistence regression suite covers:

- event-loop responsiveness and worker-owned SQLite connections;
- bounded Security concurrency and isolation from asyncio's default executor;
- one shared gate-owned Security offload with separate Approval/audit serialization domains;
- reuse across multiple event-loop lifetimes;
- repeated cancellation at a durable write boundary;
- worker-error precedence over cancellation;
- retryable busy/locked mapping;
- rollback of in-memory state after failed durable Approval writes;
- durable expiration-on-read behavior after restart;
- awaitable Control Plane Approval reads.
