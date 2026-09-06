# Performance benchmarks

Issue #440 owns repeatable evidence about platform throughput, latency, resource use and degradation. The benchmark system is deliberately separate from functional Evaluation (#19), end-to-end Conformance (#46), Observability (#16) and canonical accounting (#76/#171).

## Invariants

- Benchmarks exercise canonical platform APIs and runtime paths.
- Reference runs require no paid model or API service.
- Hardware, OS and runtime details are report metadata, not universal platform requirements.
- Correctness is checked during measurement. A fast run with dropped, duplicate or corrupted canonical work is a failed benchmark.
- Benchmark code never disables authorization, verification, durability or canonical lifecycle checks to improve numbers.
- Performance budgets are not invented globally. Baseline comparisons only classify a regression when an explicit caller-provided budget exists.

## Deterministic lifecycle baseline

`single-node.reference.lifecycle@1.0` is the deterministic baseline for the production-shaped single-node composition from #39.

Each measured operation performs authenticated Task creation, queue/start lifecycle commands, deterministic reference execution and kernel reconciliation, Task/Run reads, canonical timeline inspection and correctness verification. The reference executor performs no LLM inference, so the report characterizes platform overhead rather than provider latency.

The v1 report records throughput, p50/p95/p99 operation/admission/execution/inspection latency, process CPU, traced memory, optional peak RSS, measured storage growth, open descriptors, correctness and safe environment metadata. Setup and warmup occur before the measured resource window.

The machine-readable point schema is `docs/schemas/benchmark-report.v1.schema.json`.

```bash
platform-benchmark single-node \
  --operations 50 \
  --concurrency 10 \
  --warmup-operations 5 \
  --output artifacts/benchmarks/single-node.json
```

A supplied `--data-dir` must be a fresh, empty directory dedicated to one benchmark run.

## Concurrency sweeps

`single-node.reference.lifecycle.sweep@1.0` characterizes how the same deterministic lifecycle workload changes as concurrency rises. The default CLI sweep covers `1/10/50/100` and accepts higher explicit levels:

```bash
platform-benchmark single-node-sweep \
  --concurrency-levels 1,10,50,100 \
  --operations-per-level 100 \
  --warmup-operations 5 \
  --repetitions 3 \
  --output-dir artifacts/benchmarks/sweep
```

Every concurrency/repetition point uses a separate fresh data root. `summary.json` uses `docs/schemas/benchmark-sweep.v1.schema.json`, while every point also retains a complete v1 lifecycle report. A sweep is invalid when any point fails canonical correctness or when the environment fingerprint changes during the sweep.

## Single-node API, history and restart workloads

`platform-benchmark single-node-workload` provides four deterministic profiles using the same authenticated Control Plane and reference execution path:

- `read-heavy` rotates Task list, Task detail, nested Run list, Run detail and Task timeline reads against pre-seeded canonical state;
- `mixed` combines the same reads with weighted full Task -> Run deterministic lifecycle writes;
- `history` runs the read/query mix against a larger accumulated Task/Run/Event history; its CLI default is 1000 seeded completed Tasks unless overridden;
- `restart` seeds durable state, measures reconstruction of `SingleNodeDeployment`, reuses the persisted bearer credential, then validates and measures canonical reads after restart.

Examples:

```bash
platform-benchmark single-node-workload \
  --scenario read-heavy \
  --operations 500 \
  --concurrency 20 \
  --seed-tasks 100 \
  --output artifacts/benchmarks/read-heavy.json

platform-benchmark single-node-workload \
  --scenario mixed \
  --operations 500 \
  --concurrency 20 \
  --read-weight 4 \
  --write-weight 1 \
  --output artifacts/benchmarks/mixed.json

platform-benchmark single-node-workload \
  --scenario history \
  --operations 500 \
  --concurrency 20 \
  --seed-tasks 1000 \
  --output artifacts/benchmarks/history.json

platform-benchmark single-node-workload \
  --scenario restart \
  --operations 100 \
  --concurrency 10 \
  --seed-tasks 500 \
  --output artifacts/benchmarks/restart.json
```

Seeding and warmup happen before the measured window. The restart profile is the exception for process-composition reconstruction: the restart itself is intentionally measured and emitted as its own latency distribution.

The workload report records:

- a versioned benchmark ID/scenario;
- deployment and persistence profiles;
- workload distribution, operation count, concurrency, seed size, warmup and timeout;
- repetition semantics;
- optional-subsystem declarations;
- explicit expected correctness invariants and captured metric names;
- throughput and operation/read/write/restart p50/p95/p99 distributions;
- CPU, memory, storage and descriptor evidence;
- observed canonical Task/Run totals and duplicate-write detection;
- bounded sample canonical Task/Run references;
- errors and correctness verdict.

The schema is `docs/schemas/benchmark-workload.v1.schema.json`. One workload report represents one fresh-state repetition; repeated comparative measurements should use separate fresh data roots rather than silently reusing accumulated state.

## Baseline comparison

A prior lifecycle point report can be supplied as a comparison baseline:

```bash
platform-benchmark single-node \
  --operations 100 \
  --concurrency 10 \
  --baseline baselines/single-node-c10.json \
  --output current.json
```

Without explicit thresholds, a compatible result is `comparable_no_budget`. Evidence-backed budgets can be supplied with `--max-p95-regression-percent`, `--max-throughput-regression-percent` and `--fail-on-regression`.

A baseline is rejected as incomparable when its benchmark ID/version/profile, operation count, concurrency, warmup count or core runtime/hardware fingerprint differs. Platform version and commit may differ because cross-version comparison is the purpose of a regression baseline.

## CI tiers

- **PR smoke:** small deterministic lifecycle, tiny 1/2 sweep and tiny workload-profile fixtures; correctness and schemas mandatory, artifacts retained; no noisy universal latency budget.
- **integration/nightly/manual performance work:** full 1/10/50/100+ sweeps, realistic read/mixed/history/restart sizes and comparative baselines.
- **release qualification:** stress, soak/endurance, fault-under-load and supported distributed profiles.

Full scale, soak and saturation runs do not belong in every ordinary PR.

## Progressive #440 profiles

The suite still does not claim completion of #440. Remaining progressive work includes:

- idle-footprint evidence;
- bounded admission/persistence saturation stress profiles;
- longer-running memory/descriptor/queue soak profiles;
- deterministic fault-under-load profiles;
- durable Plan/Step long-linear, fan-out/fan-in, retry and reconciliation workloads after #384;
- local/remote Worker dispatch, heartbeat and Workspace materialization workloads after #240/#433;
- optional HA promotion profile (#89);
- optional Memory/Knowledge/Search/Connector profiles.

Each new profile must preserve the versioned evidence model and correctness rules rather than creating an unrelated benchmark methodology.
