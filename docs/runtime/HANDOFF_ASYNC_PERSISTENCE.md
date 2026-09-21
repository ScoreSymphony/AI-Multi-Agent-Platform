# Handoff async persistence

Canonical Handoff persistence runs outside asyncio request, agent, context and Control Plane hotpaths without replacing the existing Handoff lifecycle.

## Runtime boundary

`HandoffRepository` remains the synchronous setup/test/offline compatibility contract. Runtime callers use `AsyncHandoffRepository`, normally through `AsyncHandoffRepositoryAdapter` and the `HandoffService.async_*` methods.

The adapter owns no Handoff lifecycle state. It delegates to the same canonical repository used by the synchronous service, so revision, idempotency, consumption and audit semantics remain owned by the existing Handoff implementation.

## Offload policy

`HandoffPersistenceOffload` owns a dedicated `ThreadPoolExecutor` named `handoff-persistence`. It does not use asyncio's process-wide default executor. Pending submissions are bounded before executor submission, and repository work is serialized through one shared offload for all adapters wrapping the same repository object.

SQLite connections are still opened by `SQLiteHandoffRepository._connect()`. Because runtime repository methods execute entirely inside the Handoff persistence worker, connection creation, transactions and row materialization all happen outside the event loop. Constructor-time schema initialization remains an explicit synchronous bootstrap seam.

## Cancellation and failures

Once persistence has started, cancellation does not abandon the worker. The awaitable boundary shields the worker and waits for it to settle. If the worker fails while cancellation is pending, the persistence failure wins; otherwise the original cancellation is re-raised after the operation has completed.

Unhandled SQLite busy/locked failures are mapped to retryable `TRANSIENT_FAILURE`. Other unhandled SQLite failures are mapped to `BACKEND_ERROR`. Backend-neutral `ContractError` values already produced by the repository, including revision/idempotency `CONFLICT`, are preserved rather than remapped so InMemory and SQLite behavior stays aligned.

## Production consumers

The normal production paths use awaitable Handoff persistence for:

- `ProductionHandoffRuntime.create_handoff()` and `consume_handoff()`;
- Coordination binding through `CoordinatedHandoffService.async_*`;
- restart-safe consumed-Handoff Context collection;
- registered Handoff and HandoffConsumption Control Plane reads;
- the single-node operational Handoff runtime.

The synchronous `HandoffControlPlaneProjection`, synchronous `HandoffService` methods and synchronous `CoordinatedHandoffService` methods remain compatibility/offline seams and must not be called from canonical async runtime hotpaths.

## Consistency guarantees

The runtime boundary preserves read-after-write ordering by serializing repository work. SQLite transactions continue to provide rollback on failed writes. Handoff revisions and consumption bindings remain durable across repository reconstruction, and the same adapter contract is exercised against InMemory and SQLite implementations.
