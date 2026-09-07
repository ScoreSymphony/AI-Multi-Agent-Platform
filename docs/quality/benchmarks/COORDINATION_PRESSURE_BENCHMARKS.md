# Coordination pressure and recovery benchmarks

This issue #440 profile extends the durable Plan/Step graph evidence with the stateful coordination paths that become material under pressure: persisted retries, many simultaneous durable waits, and restart reconciliation. The benchmark exercises the canonical `PlatformKernel` and `DurablePlanStepCoordinator` with the reference SQLite kernel and coordinator stores.

## Profiles

`platform-coordination-pressure` exposes three deterministic profiles:

- `retry-burst`: `size` independent Steps start together, every first attempt fails with the same retryable benchmark category, all persisted retry deadlines become due together, and every second attempt succeeds;
- `deadline-wait-burst`: `size` independent running Steps enter durable deadline waits together, become due at the same logical deadline, resume the existing canonical Run attempts, and then succeed;
- `restart-reconcile`: `size` independent Steps remain active while new `SqliteKernelRepository`, `PlatformKernel`, `SQLiteCoordinatorRepository`, and `DurablePlanStepCoordinator` objects are constructed over the same durable stores; reconciliation must preserve every active Run identity before completion.

The lifecycle backend remains a deterministic local fixture. The profile therefore measures platform coordination/persistence behavior without paid services or provider-network variability.

## Attribution

The report keeps four latency classes separate:

1. `transition_latency`: per-Step retry scheduling or wait-entry latency. It is empty for restart reconciliation because there is no equivalent per-Step transition in that profile.
2. `resume_or_reconcile_latency`: one batch wakeup/reconciliation sample for the profile.
3. `outcome_persistence_latency`: canonical `PlatformKernel.record_run_outcome()` latency.
4. `coordination_observation_latency`: `DurablePlanStepCoordinator.observe_run()` latency for terminal Run outcomes.

The benchmark also records completed Steps per second, process CPU, traced memory, optional peak RSS/open-descriptor evidence, and SQLite storage growth.

## Correctness gates

A profile passes only if its scenario-specific invariants and the common lifecycle invariants hold.

### Retry burst

- every Step reaches `retry_scheduled` after the first failed canonical Run;
- no retry wakes before its persisted due time when a positive delay is configured;
- exactly one second canonical Run is created per Step;
- every second Run ID differs from its first Run ID;
- every Step reaches attempt `2` and succeeds.

### Deadline wait burst

- every Step enters a durable `deadline` wait;
- no positive-delay wait wakes before its persisted deadline;
- every Step resumes to `running` at the deadline;
- the pre-wait Run ID is preserved exactly; no replacement Run is created;
- every Step then succeeds.

### Restart reconcile

- the kernel and coordinator are reconstructed from their SQLite stores;
- reconciliation observes all active Steps as `running`;
- every pre-restart canonical Run ID is preserved exactly;
- reconciliation creates no duplicate Runs;
- completion after reconciliation succeeds normally.

For every profile, the owning Task must end in `succeeded`, the number of `run.created` events must exactly match the scenario expectation, and every created Run ID must be unique.

## Usage

```bash
platform-coordination-pressure \
  --scenario retry-burst \
  --size 64 \
  --retry-delay-seconds 1 \
  --safety-max-size 256 \
  --output artifacts/benchmarks/retry-burst-64.json

platform-coordination-pressure \
  --scenario deadline-wait-burst \
  --size 64 \
  --wait-delay-seconds 1 \
  --safety-max-size 256 \
  --output artifacts/benchmarks/deadline-wait-64.json

platform-coordination-pressure \
  --scenario restart-reconcile \
  --size 64 \
  --safety-max-size 256 \
  --output artifacts/benchmarks/restart-reconcile-64.json
```

`--retry-delay-seconds` and `--wait-delay-seconds` use a logical benchmark clock for due-time semantics; the harness does not sleep for those durations. `--safety-max-size` bounds host pressure explicitly.

The machine-readable schema is `docs/schemas/benchmark-coordination-pressure.v1.schema.json`.

## Remaining #440 durable-coordination scope

This block still does not claim concurrent multi-Plan throughput or coordinator claim/lease contention under competing coordinator instances. Those remain separate scale profiles because they need concurrency/ownership metrics that would be obscured if folded into the retry/wait/restart evidence above.
