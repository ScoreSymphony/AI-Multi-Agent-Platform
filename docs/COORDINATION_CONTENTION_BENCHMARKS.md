# Coordination contention benchmarks

This issue #440 benchmark extends the durable Plan/Step scale evidence with the two coordination paths that matter once more than one Plan and more than one coordinator instance are active: concurrent multi-Plan progress and explicit Step-claim contention. The benchmark exercises the canonical `PlatformKernel` and `DurablePlanStepCoordinator` against the reference SQLite kernel and coordinator stores.

## Profiles

`platform-coordination-contention` exposes two deterministic profiles.

### `multi-plan`

`plan_count` Tasks are planned through the canonical kernel, each with `steps_per_plan` independent Steps. Two `DurablePlanStepCoordinator` instances share one durable SQLite coordinator store and register different Plans concurrently. Every canonical Step Run is completed through the kernel and its terminal outcome is observed through one of the two coordinators.

The profile verifies that:

- every Plan remains independently addressable in the shared coordinator store;
- every Task and Step reaches `succeeded`;
- exactly one canonical Run is created per Step;
- concurrent coordination does not duplicate Run identities;
- the shared store remains readable after all concurrent work completes.

### `claim-contention`

The same multi-Plan workload is registered first. The benchmark then holds every active Step claim under coordinator A for a bounded logical TTL. Coordinator B observes the already-terminal canonical Run while that foreign claim is still live and must make no Step transition. At the exact persisted expiry boundary coordinator B observes the same Run again, takes a new fenced claim, and completes the Step normally.

The profile verifies that:

- every live foreign claim blocks coordinator B;
- blocked observation creates no replacement Run and leaves the Step `running`;
- the same observation succeeds after the claim TTL expires;
- the old claim can neither be renewed nor released after takeover;
- a subsequent audit claim has a strictly larger fence than the original stale claim;
- exactly one canonical Run still exists per Step throughout the contention/recovery cycle.

Direct repository claim acquisition is used only to establish and audit the controlled contention fixture. Measured lifecycle progress remains on `DurablePlanStepCoordinator.observe_run()` and canonical `PlatformKernel` Run/Task truth; the benchmark does not create a second lifecycle path.

## Metrics

Reports keep contention-related latency classes separate:

1. `registration_latency`: per-Plan `register_plan()` latency while Plans are registered concurrently across two coordinator instances;
2. `outcome_persistence_latency`: canonical `PlatformKernel.record_run_outcome()` latency for terminal Step Runs;
3. `blocked_observation_latency`: coordinator-B observation latency while a foreign live claim owns the Step; this distribution is empty for `multi-plan`;
4. `completion_observation_latency`: successful terminal observation latency, including fenced takeover in `claim-contention`.

The report also records completed Steps per second, process CPU, traced memory, optional peak RSS/open-descriptor evidence, and signed SQLite storage growth. Storage growth is intentionally signed because WAL/checkpoint behavior can reduce the aggregate directory size during a run.

## Correctness gates

A report passes only when all common invariants and the scenario-specific contention invariants hold.

For both profiles:

- `succeeded_tasks == plan_count`;
- `succeeded_steps == plan_count * steps_per_plan`;
- `run.created` count equals the total Step count;
- all created Run IDs are unique;
- no benchmark exception was recorded.

For `claim-contention`, the total Step count must additionally equal each of:

- `blocked_observations`;
- `recovered_observations`;
- `stale_claim_rejections`;
- `fence_advanced_steps`.

For `multi-plan`, all four contention-only counters must remain zero.

## Usage

```bash
platform-coordination-contention \
  --scenario multi-plan \
  --plan-count 16 \
  --steps-per-plan 16 \
  --safety-max-total-steps 512 \
  --output artifacts/benchmarks/multi-plan-16x16.json

platform-coordination-contention \
  --scenario claim-contention \
  --plan-count 8 \
  --steps-per-plan 16 \
  --claim-hold-seconds 1 \
  --safety-max-total-steps 256 \
  --output artifacts/benchmarks/claim-contention-8x16.json
```

`--claim-hold-seconds` controls logical claim time. The harness does not sleep for the TTL; it evaluates one observation before expiry and one at the expiry boundary. `--safety-max-total-steps` explicitly bounds the product of Plans and Steps per Plan.

The machine-readable schema is `docs/schemas/benchmark-coordination-contention.v1.schema.json`.

## #440 scope after this block

Together with the previously merged retry/wait/restart profiles, this benchmark closes the planned single-node durable-coordination evidence for simultaneous Plans and explicit coordinator claim/fence contention. Any later distributed coordinator scale claim should be treated separately because multi-host scheduling, network partitions and remote persistence would introduce different ownership and failure semantics than this single-node reference profile.
