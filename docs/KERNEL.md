# Task / Run / Event Kernel

The platform kernel is the smallest component that owns canonical Task and Run lifecycle decisions. It is intentionally independent from Hermes, Forge, Temporal and concrete persistence products.

## Responsibilities

The kernel owns:

- task creation, updates and queries;
- deterministic Task lifecycle transitions;
- canonical Run identity and attempt numbering;
- run start, observation/completion and cancellation;
- canonical lifecycle event emission;
- correlation and causation metadata;
- command idempotency and concurrent retry handling;
- artifact and result references;
- reconstruction of externally visible state from event history;
- restart recovery and reconciliation of incompletely observed lifecycle transitions.

The kernel does **not** own model reasoning, backend process execution or a specific production database.

## Dependency boundaries

The kernel depends only on platform contracts:

```text
PlatformKernel
  ├─ Orchestrator
  ├─ LifecycleBackend
  └─ EventProvider
```

A deployment may replace any implementation of those contracts without changing canonical Task or Run identifiers.

## Event history as reconstructable truth

`TaskView` and `RunView` are derived by deterministic reducers. They are not authoritative mutable records.

The reducers consume canonical events in stable order and reconstruct visible state. This provides a recovery path independent from a specific database schema and lets future read models be rebuilt from history.

Initial Task events include:

- `task.created`
- `task.updated`
- `task.ready`
- `task.running`
- `task.succeeded`
- `task.failed`
- `task.cancelled`
- `plan.created`
- `artifact.attached`
- `result.recorded`

Initial Run events include:

- `run.queued`
- `run.running`
- `run.succeeded`
- `run.failed`
- `run.cancelled`
- `run.timed_out`

## Correlation and causation

All events for a Task use the canonical Task ID as their initial `correlation_id`.

User/API commands supply a stable `command_id`. Events caused by that command carry it as `causation_id`; the event that reserves or completes the command also stores it in its payload for idempotent command lookup.

This is deliberately separate from future distributed tracing identifiers.

## Idempotent commands and concurrent retries

A command ID represents one logical mutation. Retrying a command with the same ID must resolve to the already-created canonical state rather than allocate another Task or Run.

Command-bearing events receive deterministic event IDs. The `EventProvider.publish()` contract requires repeated publication of the same `event_id` to be idempotent: only one canonical event may exist for that identifier.

Run starts additionally derive the canonical Run ID deterministically from the Task ID plus command ID. This means two processes that race after both observing the same pre-command history still converge on the same Run identity.

The critical start flow is:

1. validate and reconstruct the current Task;
2. obtain the replaceable orchestration plan;
3. re-check whether another retry already reserved the command;
4. derive the canonical Run ID from Task ID plus command ID;
5. atomically reserve the command by publishing deterministic `run.queued`;
6. reconstruct the reservation from persisted history;
7. move the Task to `running` when required;
8. reconcile with the lifecycle backend using that exact Run ID;
9. persist only backend state that has actually been observed.

The baseline SQLite provider enforces event-ID uniqueness in the database, so simultaneous command retries cannot create two canonical `run.queued` reservations.

## Backend start semantics

`LifecycleBackend.start()` returns an `ExecutionHandle`, not a guarantee that execution is already running. A backend may accept a Run while still reporting `queued`.

The kernel therefore does **not** emit `run.running` merely because `start()` returned successfully. It observes the backend through `LifecycleBackend.get()` and records `run.running` only when a normalized `RUNNING` snapshot is actually seen. An immediately terminal snapshot can transition directly from queued to the corresponding terminal Run event.

## Restart and split-event recovery

For a Run whose last canonical state is `queued`, recovery first asks the `LifecycleBackend` for that same canonical Run ID.

- If the backend already knows the Run, the kernel records its observed normalized state and does **not** allocate another Run.
- If the backend reports `not_found`, the kernel may call `start()` again using the same canonical Run ID. The lifecycle contract requires repeated starts for that Run identity to be idempotent.
- If a just-started backend still reports `queued`, the canonical Run remains queued until later observation confirms progress.

This handles the ambiguous crash window where a backend accepted work but the platform died before persisting the next Run state.

Recovery also repairs split event boundaries. If a terminal Run event was persisted but the process died before the matching terminal Task event, a retry or recovery pass reconciles the Task from the already-terminal canonical Run instead of returning early and leaving the Task stuck in `running`.

A backend may already be terminal when recovery first observes it. Therefore the canonical reducer permits `queued -> terminal` transitions when an intermediate running observation was never persisted.

## Persistence

The production event-store technology is deliberately not selected by issue #6. `EventProvider` remains the replaceable boundary.

A small standard-library SQLite implementation exists under `ai_multi_agent_platform.testing.sqlite_events` to verify durable restart, cursor and concurrent reservation behavior. It is a reference/testing provider, not a production database decision.

## Artifacts and results

Issue #6 stores only canonical references (`artifact_ref`, `result_ref`) in lifecycle history. Payload storage and richer metadata belong behind later File/Artifact and knowledge/storage work.

## Non-goals

This kernel does not implement:

- Hermes integration;
- Forge integration;
- Temporal workflows;
- production database selection;
- distributed worker scheduling;
- authorization policy;
- model routing;
- MCP transport.

Those components must integrate around the kernel rather than replace its canonical lifecycle ownership.
