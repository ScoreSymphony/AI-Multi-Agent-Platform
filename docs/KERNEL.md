# Platform-owned Task / Run / Event Kernel

The kernel is the authoritative owner of externally visible Task and Run lifecycle state. Hermes, Forge, Temporal, executors, model providers and other adapters may participate in planning or execution, but none of their private databases or status models become the platform source of truth.

## Ownership boundary

The implementation lives under `ai_multi_agent_platform.kernel` and depends only on the canonical domain model plus replaceable platform contracts.

```text
PlatformKernel
  ├─ canonical domain: Task, Run, TaskStatus, RunStatus
  ├─ EventRepository          <- authoritative persisted history
  ├─ TaskRepository           <- replaceable read boundary
  ├─ RunRepository            <- replaceable read boundary
  ├─ Orchestrator             <- replaceable planning participant
  ├─ LifecycleBackend         <- replaceable execution participant
  └─ EventProvider (optional) <- post-commit mirror, never source of truth
```

`InMemoryKernelRepository` is the deterministic baseline. `SqliteKernelRepository` is a durable stdlib reference implementation used to prove process restart and recovery semantics. Neither selects the final production database.

## Canonical lifecycle

The kernel reuses the canonical state machines from `ai_multi_agent_platform.domain`; it does not introduce parallel Task or Run status definitions.

Tasks support draft, ready, running, waiting/blocked, succeeded, failed and cancelled flows. Runs support queued, starting, running, succeeded, failed, cancelled and timed-out flows. Illegal transitions fail with a canonical `ContractError(CONFLICT)`.

A failed Task may be retried. Retry transitions the Task back to ready and creates a **new** canonical Run with an incremented attempt number. Previous attempts remain immutable history.

### Task/run consistency and step-run semantics

Direct `complete_task()` and `fail_task()` operations are rejected while any canonical run is still non-terminal. Task terminal state therefore cannot diverge from an active run, and task retry is likewise rejected while unfinished work remains.

Attempts are scoped to the canonical run subject `(subject_type, subject_id)`, not to the task-wide number of runs. Task runs may be created/started only while the task is `ready`. Step runs may be created/started while the task is `ready` or `running`; their success, failure, timeout or cancellation terminalizes the step run only and does not implicitly terminalize the parent task. Cancelling the parent task cancels every non-terminal run first (queued runs canonically, starting/running runs through the lifecycle backend) and only then terminalizes the task. Parallel step runs are supported for distinct step subjects, but task-level and step-level execution modes cannot be active at the same time. A second non-terminal run for the same subject is rejected.

## Event history and read models

Every mutation appends one or more canonical `PlatformEvent` records. Task and Run views are reconstructed deterministically from the ordered event stream; mutable adapter state is not read as canonical platform state.

Lifecycle events include, among others:

- `task.created`, `task.updated`, `task.ready`, `task.running`, `task.waiting`, `task.resumed`, `task.succeeded`, `task.failed`, `task.cancelled`;
- `plan.created`;
- `run.created`, `run.starting`, `run.dispatch_attempted`, `run.running`, `run.succeeded`, `run.failed`, `run.cancelled`, `run.timed_out`;
- `run.recovery_required` / `run.recovery_cleared`;
- `artifact.attached` and `result.attached`.

Each generated event carries an event ID, event type, canonical subject, timestamp, task correlation ID, command/recovery causation ID, owner metadata, actor/source metadata, canonical payload version and deterministic stream revision. Adapter-private diagnostics remain in namespaced `AdapterMetadata`.

Artifact and Result payloads are referenced by canonical `artifact_*` / `result_*` IDs. The kernel deliberately does not choose the file or result-storage backend.

## Idempotency and consistency

Every retriable mutation requires an idempotency key. The repository stores an atomic `CommandRecord` together with the first event(s) of that command. Replaying the same key for the same operation returns the existing result; reusing that key for a different operation is a conflict.

The repository commit boundary also requires an `expected_revision`. A stale writer is rejected before new events are appended. This gives the baseline an optimistic-concurrency mechanism without coupling the kernel to a specific production database.

Run dispatch is ordered deliberately:

1. persist the canonical `run.starting` transition and reserve the start command;
2. persist `run.dispatch_attempted`;
3. call the replaceable lifecycle backend using the already-persisted canonical Run ID;
4. persist the normalized backend observation (`run.running` or a later terminal state).

That ordering makes duplicate starts detectable and makes the crash window recoverable.

## Cancellation and duplicate callbacks

Cancellation before dispatch is entirely canonical and does not call the backend. Cancellation of a starting/running Run asks the backend to cancel the same canonical Run ID and maps the normalized result back into platform state.

Cancellation is safe to repeat. Duplicate terminal callbacks for the same outcome are accepted without adding a second terminal transition. A contradictory terminal callback is rejected rather than rewriting history.

## Recovery and reconciliation

`recover_task()` classifies every non-terminal attempt after restart:

| Canonical Run state | Recovery behavior |
| --- | --- |
| `queued` | Keep pending; it was never dispatched. |
| `starting`, backend knows Run | Reconcile the backend snapshot into canonical state without dispatching again. |
| `starting`, backend does not know Run | Re-dispatch the **same canonical Run ID**; the extra dispatch attempt is persisted. |
| `running`, backend knows Run | Reconcile the normalized snapshot. |
| `running`, backend does not know Run | Mark `recovery_required`; do **not** blindly create or start another Run. |
| terminal | Leave unchanged. |

This distinguishes a crash before backend acceptance from a crash after acceptance. It also leaves an explicit reconciliation marker for orphaned external jobs that future backend-specific reconcilers can resolve.

`recover_all()` applies the same logic to all task streams in the configured repository.

## Fake/reference end-to-end flow

With only the reference providers the kernel can:

1. create and ready a canonical Task;
2. obtain a plan from `FakeOrchestrator`;
3. create a canonical Run;
4. dispatch it through `FakeLifecycleBackend`;
5. observe success, failure, timeout or cancellation;
6. attach canonical Artifact/Result IDs;
7. transition Run and Task consistently;
8. replay the event history to the same visible state.

No Hermes, Forge, Temporal, external model API or production database is required for this path.

## Deliberate non-goals

Issue #6 does not select a production scheduler, event bus or database and does not integrate Hermes, Forge or Temporal. Those components must remain replaceable participants around this kernel and map their private state into the platform-owned canonical lifecycle.
