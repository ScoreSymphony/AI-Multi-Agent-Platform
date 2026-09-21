# Security Approval async persistence

Runtime-critical async code avoids inline synchronous SQLite work while retaining SQLite as a first-class single-node backend.

## Runtime boundary

Security keeps the existing synchronous `ApprovalService`, `SqliteApprovalService`, and `SqliteAuthorizationAuditSink` surfaces for setup, offline tooling, compatibility, and direct persistence tests. Async production runtime callers use backend-neutral awaitable contracts instead:

- `AsyncApprovalService` for approval resolution, inspection, creation, decision, and cancellation;
- `AsyncAuthorizationAuditSink` for durable authorization-audit appends.

`AuthorizationGate` owns these runtime facades. When only the existing synchronous stores are supplied, it automatically composes adapters for them, so callers of `decide()`, `enforce()`, `decide_approval()`, `cancel_approval()`, and `ensure_pending_approval_with_event()` do not depend on SQLite or on a thread-offload implementation.

Northbound Approval resource reads, Approval decision composition, Learning/Governance/Planning/Capability runtime checks, Learning Control Plane projections, egress/evaluation consumers, and policy-profile Approval lookups use the awaitable Approval facade rather than calling the synchronous service directly. The synchronous Approval API remains an explicit offline/setup/test/compatibility seam; it is not the persistence surface for async production runtime paths.

Evaluation also retains compatibility with older structural Approval readers whose `all()` is a regular callable. That callable is invoked through one shared bounded `SecurityPersistenceOffload`; if it returns an awaitable (for example because an async implementation is wrapped by a decorator), the result is normalized and awaited after the offloaded invocation. Compatibility therefore neither reintroduces inline event-loop work nor depends on asyncio's default executor. A real `ApprovalService` is still routed through `AsyncApprovalServiceAdapter` and therefore keeps the per-backing-service serialization ownership described below.

## Executor and connection ownership

`SecurityPersistenceOffload` owns a bounded `ThreadPoolExecutor`. It does not use asyncio's process-wide default executor, so a Security persistence backlog cannot consume worker capacity needed by unrelated runtime work.

SQLite connections remain worker-owned because every synchronous repository operation, including connection creation, executes inside that executor. The offload object is independent of an event loop and can therefore be reused across sequential `asyncio.run(...)` lifetimes.

Approval serialization ownership follows the backing `ApprovalService`, not an individual adapter. Independently created `AsyncApprovalServiceAdapter` instances for the same backing service resolve to the same effective Security offload and therefore the same `approvals` serialization lock. This also applies when separate `AuthorizationGate` or Control Plane objects wrap the same Approval service. The registry is weak-keyed by the backing service, so it does not extend that service's lifetime; once the backing service and its adapters become unreachable, the shared offload can be collected rather than becoming a process-lifetime executor leak.

A default `AuthorizationGate` also uses that Security offload for its authorization-audit adapter. Approval operations and audit appends target separate SQLite databases and therefore use separate serialization domains while sharing the same bounded worker pool.

## Approval serialization and expiration

All runtime Approval operations use the `approvals` serialization domain, including reads. This is intentional: `ApprovalService.get()` may transition an expired pending Approval to `EXPIRED`, and `all()`, `pending_for()`, `find_valid_for()`, and `valid_for()` may reach that path. Treating those reads as part of the same domain prevents expiration persistence from racing with request, decision, or cancellation mutations.

The shared per-backing-service domain also prevents another async adapter from observing an in-memory mutation before the corresponding SQLite write has committed. A write and every awaitable read of that same Approval service cross one ordered persistence boundary.

Authorization-audit writes use their own `audit` serialization domain. Audit storage is append-only and independent from Approval state, so a blocked Approval operation does not acquire the audit lock and vice versa.

## Cancellation and error semantics

Worker-side persistence remains shielded until its durable boundary settles. In addition, Approval mutations whose committed state requires a lifecycle notification or authorization audit use a wider Security completion boundary: once the mutation is in flight, caller cancellation is deferred until the durable Approval operation and its required post-commit event/audit sequence have finished. This prevents a client disconnect from leaving a durable pending/resolved Approval without the corresponding required-attention/resolved notification or audit. Repeated cancellation is still deferred, and an inner persistence/audit failure wins over the pending cancellation so callers do not lose the authoritative failure.

SQLite `busy` and `locked` operational failures map to retryable `TRANSIENT_FAILURE`; other SQLite failures map to `BACKEND_ERROR`. Existing domain errors such as `NOT_FOUND`, `CONFLICT`, and `FORBIDDEN` are preserved.

`SqliteApprovalService` also restores its in-memory record state when a durable request/decision/cancellation/expiration write fails. The synchronous store therefore cannot report a mutation from memory that was not committed to SQLite.

## Backend neutrality

Async Security callers depend only on the awaitable protocols. A future PostgreSQL implementation can provide those protocols directly without changing `AuthorizationGate`, Approval Control Plane resources, or Approval decision command callers.

## Regression coverage

The Security persistence regression suite covers:

- event-loop responsiveness and worker-owned SQLite connections;
- bounded Security concurrency and isolation from asyncio's default executor;
- one shared gate-owned Security offload with separate Approval/audit serialization domains;
- shared Approval serialization across independently created adapters and multiple gates;
- Gate/Control Plane reads waiting until a concurrent Approval write crosses its durable boundary;
- weak backing-service ownership so shared offloads do not become process-lifetime registry leaks;
- reuse across multiple event-loop lifetimes;
- repeated cancellation at a durable write boundary;
- cancellation after an Approval mutation starts still completing required/resolved events and durable audit before cancellation propagates;
- worker/error precedence over pending cancellation;
- retryable busy/locked mapping;
- rollback of in-memory state after failed durable Approval writes;
- durable expiration-on-read behavior after restart;
- awaitable Control Plane Approval reads and async production consumer routing;
- synchronous structural Evaluation Approval readers and regular callables returning awaitables remaining compatible while executing through the bounded Security offload.
