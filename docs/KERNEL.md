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
- command idempotency;
- artifact and result references;
- reconstruction of externally visible state from event history;
- restart recovery for incompletely observed Run starts.

The kernel does **not** own model reasoning, backend process execution or a specific database.

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

The reducer consumes canonical events in stable order and reconstructs the visible state. This provides a recovery path independent from a specific database schema and lets future read models be rebuilt from history.

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

User/API commands supply a stable `command_id`. Events caused by that command carry it as `causation_id`; the event that marks command completion also stores it in its payload for idempotent command lookup.

This is deliberately separate from future distributed tracing identifiers.

## Idempotent commands

A command ID represents one logical mutation. Retrying a command with the same ID must resolve to the already-created canonical state rather than allocate another Task or Run.

The critical example is Run start:

1. create canonical `run_id`;
2. persist `run.queued` with the command ID;
3. move the Task to `running`;
4. ask the replaceable lifecycle backend to start that exact Run ID;
5. persist the observed backend state.

If the process dies after step 2, 3 or 4, replaying the same command resolves to the existing Run ID.

## Restart recovery

For a Run whose last canonical state is `queued`, recovery first asks the `LifecycleBackend` for that same canonical Run ID.

- If the backend already knows the Run, the kernel records its current normalized state and does **not** start another execution.
- If the backend reports `not_found`, the kernel may start the same canonical Run ID again.

This handles the ambiguous crash window where a backend accepted work but the platform died before persisting `run.running`.

A backend may already be terminal when recovery first observes it. Therefore the canonical reducer permits `queued -> terminal` transitions when the intermediate running observation was lost.

## Persistence

The production event-store technology is deliberately not selected by issue #6. `EventProvider` is the boundary.

A small standard-library SQLite implementation exists under `ai_multi_agent_platform.testing.sqlite_events` to verify durable restart behavior. It is a reference/testing provider, not a production architecture decision.

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
