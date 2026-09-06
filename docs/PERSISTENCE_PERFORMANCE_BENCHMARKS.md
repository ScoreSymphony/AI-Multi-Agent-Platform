# Persistence growing-state benchmarks

This document extends the performance methodology owned by issue #440 with a repeatable growing-state profile for the supported single-node reference deployment.

## Scope

`single-node.persistence.growing-state.sweep@1.0` characterizes how canonical query and restart behavior changes as durable Task/Run/Event state grows. It deliberately drives the existing authenticated Control Plane and deterministic reference execution path through `SingleNodeWorkloadHarness`; it does not benchmark SQLite repositories directly as the primary evidence.

Every state-size point is created in an independent fresh data root. The harness seeds the requested number of completed canonical Tasks/Runs, reconstructs the supported `SingleNodeDeployment` on that durable state, reuses persisted authentication, then executes the standard deterministic query mix after restart.

This provides comparable evidence for:

- read/query p50/p95/p99 latency as durable state grows;
- throughput of the canonical post-restart query mix;
- restart/open latency with non-trivial durable state;
- total persisted data-directory size and bytes per seeded Task;
- observed canonical Task/Run totals after restart;
- correctness at every scale point;
- safe environment and platform metadata for later comparison.

The profile is intentionally provider-neutral at the platform boundary. `sqlite-reference` is recorded as the current persistence profile, not declared as a canonical backend requirement.

## Running the sweep

The profile is available through the same `platform-benchmark` entrypoint as the other #440 benchmark families:

```bash
platform-benchmark single-node-persistence-sweep \
  --seed-task-levels 10,100,1000 \
  --operations-per-level 100 \
  --concurrency 10 \
  --warmup-operations 5 \
  --repetitions 3 \
  --output-dir artifacts/benchmarks/persistence-growing-state
```

`--data-root` is optional. When supplied, it must be fresh and empty. Every `seed_tasks/repetition` combination receives its own child data root, avoiding cross-point contamination.

The default state levels are `10,100,1000`; larger manual/nightly/release sweeps should choose levels appropriate for the documented reference environment rather than treating these defaults as universal capacity claims.

## Result artifacts

`summary.json` conforms to `docs/schemas/benchmark-persistence-sweep.v1.schema.json` and contains one point for every state-size/repetition pair. Each point references a full `benchmark-workload.v1` report containing the underlying operation/resource/correctness evidence.

A point is invalid if canonical workload correctness fails. The whole sweep is also invalid if the safe environment fingerprint changes between points.

The summary records:

- `seed_task_levels` and repetitions;
- operation count and concurrency used for the post-restart query phase;
- throughput;
- read p50/p95/p99;
- restart p50;
- persisted storage bytes and storage bytes per seeded Task;
- observed canonical Task/Run totals;
- per-point and aggregate correctness.

## Interpretation

This sweep closes part of #440's persistence-scalability evidence: growing-state query behavior, database/file growth, and restart/open cost can now be measured as explicit state-size curves rather than isolated anecdotal runs.

It does **not** yet prove:

- concurrent persistence lock/contention behavior under deliberately saturated writers;
- transaction retry semantics under injected persistence failure;
- cleanup/retention effects where no stable supported retention path exists;
- transient persistence-failure recovery under load.

Those require a stable provider-/fixture-level failure seam. The benchmark suite must not add a production bypass or couple platform contracts to SQLite merely to manufacture those faults.

## Suggested evidence tiers

For pull-request or local semantic validation, use very small levels such as `1,2` with a few operations. These runs prove harness/report correctness, not a production operating envelope.

For integration/nightly performance work, use repeated logarithmic state levels such as `10,100,1000,5000` where the host budget permits. For release evidence, retain the full summary and point reports together with the platform commit and environment metadata, then derive operating-envelope statements from measured curves rather than from hardware assumptions.
