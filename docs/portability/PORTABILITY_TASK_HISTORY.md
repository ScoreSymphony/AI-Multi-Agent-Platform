# Portable Task and Run history

Issue #79 treats historical Task/Run portability as archival interchange, not as a way to recreate live execution state.

## Boundary

The portable resource type is `task_history`. Its identity policy is always `historical_preserve`.

A history snapshot is produced from the canonical Task event stream and reducer projections only when:

- the Task is terminal (`succeeded`, `failed`, or `cancelled`); and
- every Run referenced by that Task is terminal (`succeeded`, `failed`, `cancelled`, or `timed_out`).

Draft, ready, running, or waiting Tasks are rejected rather than implicitly quiesced. A terminal Task that still references a non-terminal Run is also rejected.

## Preserved history

The portable snapshot preserves:

- the canonical terminal `Task` projection and revision;
- canonical Run projections and revisions;
- Run output metadata;
- Task/Run Artifact and Result references;
- Plan and Step references;
- canonical lifecycle event order and event payloads;
- timestamps, provenance, owner references, schema versions, and durable external references.

Project, Goal, Plan, Step, Artifact, and Result references are declared as package dependencies. Project scope is required when present; historical contextual references such as Goal/Plan/Step/Artifact/Result may be absent from the package and are reported as optional missing dependencies.

## Runtime-state exclusion

Historical portability never exports execution authority. In particular:

- `RunState.backend_ref` is not part of the portable Run projection;
- `RunState.dispatch_attempts`, recovery flags, and recovery reasons are not portable;
- `Run.trace_id` and `Run.worker_id` are removed from the historical Run snapshot;
- `Event.trace_id` is removed from the historical event timeline;
- live leases, reservations, backend-private job databases, active sessions, and process state remain outside the package boundary;
- ordinary package secret/runtime-state validation still applies to all serialized metadata and event payloads.

Durable adapter provenance should use canonical `ExternalRef` metadata rather than becoming portable identity.

## Import destination

Imported Task history is written only through `HistoricalTaskArchiveRepository`. The portability mutation handler has no live `EventRepository.commit()` capability and therefore cannot make an imported Task runnable, schedulable, recoverable, or dispatchable.

The reference `InMemoryHistoricalTaskArchiveRepository` demonstrates the contract. Production deployments may provide another implementation behind the same platform-owned interface.

The live Task event store and historical archive are intentionally separate persistence boundaries.

## Identity and conflicts

Historical Task identity is preserved. `ImportPreviewService` therefore reports an existing archive identity as a conflict rather than silently cloning it. Nested Run IDs remain historical canonical IDs; typed Project/Goal/Plan/Step/Artifact/Result references use the accepted `ImportContext` mapping where corresponding resources are imported together.

## Rollback

`TaskHistoryImportMutationHandler` participates in the standard #79 `ImportExecutor` transaction model. A completed archive write returns the Task ID as its rollback token; if a later package resource fails, reverse compensation deletes the imported archive entry.

Rollback never mutates the live kernel because the live kernel is not an import target for `task_history`.

## Required invariant

After importing a `task_history` resource into an otherwise empty installation:

- the historical archive contains the terminal Task history; and
- the live kernel still contains no Task stream for that imported history.

This invariant is covered by the issue #79 regression tests and is the concrete meaning of “task-history import remains historical.”
