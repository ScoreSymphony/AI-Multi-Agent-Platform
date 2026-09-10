# SQLite writer-contention benchmarks

Issue #440 requires persistence evidence under deliberate concurrent writer saturation in addition to growing-state and injected-fault profiles. `platform-persistence-contention` exercises that gap through the real canonical kernel and the stdlib SQLite reference repository.

## What the profile measures

The harness creates multiple independent `PlatformKernel` + `SqliteKernelRepository` instances that share one SQLite database. Repository instances are initialized before measurement, then each writer runs on its own thread and synchronizes with every other writer before each canonical Task mutation.

Each Task performs:

1. `PlatformKernel.create_task(...)`;
2. `PlatformKernel.ready_task(...)`.

The SQLite repository continues to use its production reference behavior: WAL mode, a fresh connection per operation and `BEGIN IMMEDIATE` for commits. The benchmark does not alter the SQLite busy timeout, hold synthetic private locks, write benchmark-only database rows or bypass the `EventRepository`/kernel path.

Because SQLite serializes competing writers internally, the report treats end-to-end canonical mutation latency as the observable contention latency. It also records:

- logical mutation throughput;
- p50/p95/p99 mutation latency;
- synchronized writer count and rounds;
- peak concurrently in-flight canonical mutations;
- surfaced SQLite `busy`/`locked` failures;
- canonical `CONFLICT` failures;
- unexpected errors;
- successful operations per writer;
- CPU, traced memory, peak RSS, open-descriptor and storage evidence;
- post-reopen Task/Event/idempotency integrity.

Any dropped mutation, unexpected conflict, SQLite busy failure or reopened-state mismatch fails benchmark correctness. The harness does not add retry behavior that the production kernel does not own.

## Running it

```bash
platform-persistence-contention \
  --writers 4 \
  --tasks-per-writer 8 \
  --output artifacts/benchmarks/persistence-contention.json
```

Safety bounds are explicit and configurable:

```bash
platform-persistence-contention \
  --writers 8 \
  --tasks-per-writer 20 \
  --safety-max-writers 8 \
  --safety-max-tasks-per-writer 20 \
  --barrier-timeout-seconds 10 \
  --output artifacts/benchmarks/persistence-contention.json
```

Use a fresh `--data-dir` when retaining the SQLite files. Omitting it uses a temporary directory.

## Result contract

Reports use benchmark ID `persistence.sqlite.writer-contention`, benchmark version `1.0` and schema:

`docs/schemas/benchmark-persistence-contention.v1.schema.json`

The report identifies the SQLite path as a **reference persistence profile only**. Results must not be generalized to other persistence providers or hardware.

## PR smoke versus operating-envelope evidence

The dedicated PR workflow uses only a tiny synchronized fixture. It proves composition, schema, correctness and that multiple canonical writers can compete safely; it is not a capacity claim.

Release/manual evidence should use repeated larger writer/task sweeps on documented reference hosts and retain the machine-readable reports. Only those comparable real measurements should be used to establish a persistence operating envelope or a future versioned performance budget.

## Relationship to other #440 persistence evidence

- `single-node-persistence-sweep` characterizes query/restart/storage behavior as durable state grows.
- `platform-persistence-fault` characterizes deterministic before-/after-commit failure recovery at the `EventRepository` boundary.
- `platform-persistence-contention` characterizes real competing SQLite writers without injected repository failures.

These profiles deliberately remain separate so state-size effects, injected failure semantics and real SQLite writer contention are not conflated.
