# Performance benchmarks

Issue #440 owns repeatable evidence about platform throughput, latency, resource use and degradation. The benchmark system is deliberately separate from functional Evaluation (#19), end-to-end Conformance (#46), Observability (#16) and canonical accounting (#76/#171).

## Invariants

- Benchmarks exercise canonical platform APIs and runtime paths.
- Reference runs require no paid model or API service.
- Hardware, OS and runtime details are report metadata, not universal platform requirements.
- Correctness is checked during measurement. A fast run with dropped, duplicate or corrupted canonical work is a failed benchmark.
- Benchmark code never disables authorization, verification, durability or canonical lifecycle checks to improve numbers.
- Performance budgets are not invented globally. Baseline comparisons only classify a regression when an explicit caller-provided budget exists.

## First benchmark profile

`single-node.reference.lifecycle@1.0` is the deterministic baseline for the production-shaped single-node composition from #39.

Each measured operation performs:

1. authenticated `POST /api/v1/tasks`;
2. authenticated canonical Task queue command;
3. authenticated canonical Task start command;
4. deterministic reference execution and kernel reconciliation;
5. authenticated Task detail read;
6. authenticated Run detail read;
7. authenticated canonical Task timeline read;
8. correctness verification for terminal Task/Run state and timeline evidence.

The reference executor performs no LLM inference, so the report characterizes platform overhead rather than provider latency.

## Metrics emitted

The v1 report records:

- completed operations per second;
- end-to-end operation latency p50/p95/p99;
- Task admission latency p50/p95/p99;
- execution/reconciliation latency p50/p95/p99;
- canonical inspection latency p50/p95/p99;
- process CPU seconds during the measured window;
- Python traced current/peak memory;
- peak resident set size where the host exposes it;
- data-root bytes immediately before/after the measured window and measured storage growth;
- open file descriptor count where the host exposes `/proc/self/fd`;
- attempted/completed/failed operation counts;
- duplicate Task/Run ID detection;
- timeline correctness failures;
- safe environment fingerprint metadata, including CPU model/count and host memory where available.

Setup and warmup happen before CPU, traced-memory and storage-growth measurement starts. Unavailable host-specific metrics are reported as `null`; the harness does not fabricate values.

The machine-readable point schema is `docs/schemas/benchmark-report.v1.schema.json`.

## Local use

Run a small deterministic benchmark:

```bash
platform-benchmark single-node \
  --operations 50 \
  --concurrency 10 \
  --warmup-operations 5 \
  --output artifacts/benchmarks/single-node.json
```

If `--data-dir` is omitted, the command uses an isolated temporary data root and removes it after the report is written. A supplied `--data-dir` must be a fresh, empty directory dedicated to one benchmark run. This protects unrelated platform state, prevents benchmark credentials from being reused, and gives every reference run the same clean persistence starting point.

## Concurrency sweeps

`single-node.reference.lifecycle.sweep@1.0` characterizes how the same deterministic lifecycle workload changes as concurrency rises. The default CLI sweep covers the issue-required `1/10/50/100` levels and can be extended beyond 100 explicitly:

```bash
platform-benchmark single-node-sweep \
  --concurrency-levels 1,10,50,100 \
  --operations-per-level 100 \
  --warmup-operations 5 \
  --repetitions 3 \
  --output-dir artifacts/benchmarks/sweep
```

Every concurrency/repetition point uses a separate fresh single-node data root. This is intentional: state accumulated by the 1-concurrency point must not silently make the 100-concurrency point a different persistence-history workload.

The output directory contains:

- `summary.json` using `docs/schemas/benchmark-sweep.v1.schema.json`;
- one complete v1 point report such as `c-10-r-2.json` for every concurrency/repetition pair.

The summary records the deployment profile, reference persistence profile, deterministic workload distribution, environment fingerprint, operation count, warmup count, timeout, repetition count, requested concurrency levels, throughput, p95 latency, storage growth and correctness for every point. Full point reports preserve all other distributions and resource evidence.

A sweep is invalid when any point fails canonical correctness or when the environment fingerprint changes during one sweep. Repetitions are evidence samples, not permission to average away a failed correctness run.

Absolute numbers from different hardware must not be compared as if the machines were identical. The intended result is an environment-specific operating curve, not a universal capacity claim.

## Baseline comparison

A prior single-point report can be supplied as a comparison baseline:

```bash
platform-benchmark single-node \
  --operations 100 \
  --concurrency 10 \
  --baseline baselines/single-node-c10.json \
  --output current.json
```

Without explicit thresholds, a compatible result is classified as `comparable_no_budget`. This is intentional: #440 requires measured, justified budgets rather than arbitrary round numbers.

After a project/release has adopted evidence-backed budgets, callers can opt into regression classification:

```bash
platform-benchmark single-node \
  --operations 100 \
  --concurrency 10 \
  --baseline baselines/single-node-c10.json \
  --max-p95-regression-percent 15 \
  --max-throughput-regression-percent 10 \
  --fail-on-regression \
  --output current.json
```

A baseline is rejected as incomparable when its benchmark ID/version/profile, operation count, concurrency, warmup count or core runtime/hardware fingerprint differs. Platform version and commit are intentionally allowed to differ because cross-version comparison is the purpose of a regression baseline.

## CI tiers

The intended tiers are:

- **PR smoke:** small deterministic workload, correctness mandatory, report uploaded as an artifact; no noisy universal latency budget.
- **integration/nightly:** larger concurrency sweeps, read/write mixes, persistence growth and comparative baselines.
- **release qualification:** stress, soak/endurance, restart/recovery under load and the supported distributed profiles.

The `single-node-sweep` command is suitable for integration/nightly or explicit performance work. The ordinary PR workflow keeps the smaller single-point smoke so that 100-concurrency measurements do not turn every unrelated pull request into a load test.

Full soak and saturation runs do not belong in every ordinary PR.

## Progressive #440 profiles

The current suite does not claim completion of the whole issue. Add the following as their dependencies stabilize:

- read-heavy and mixed read/write API profiles;
- large persisted Task/Run/Event history profiles;
- restart with accumulated durable state;
- idle-footprint evidence;
- bounded admission/persistence saturation stress profiles;
- longer-running memory/descriptor/queue soak profiles;
- deterministic fault-under-load profiles;
- durable Plan/Step long-linear, fan-out/fan-in, retry and reconciliation workloads after #384;
- local/remote Worker dispatch, heartbeat and Workspace materialization workloads after #240/#433;
- optional HA promotion profile (#89);
- optional Memory/Knowledge/Search/Connector profiles.

Each new profile must preserve the versioned evidence model and correctness rules rather than creating an unrelated benchmark methodology.
