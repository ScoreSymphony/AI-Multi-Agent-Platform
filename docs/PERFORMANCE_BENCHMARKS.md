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
- data-root bytes before/after and storage growth;
- open file descriptor count where the host exposes `/proc/self/fd`;
- attempted/completed/failed operation counts;
- duplicate Task/Run ID detection;
- timeline correctness failures;
- safe environment fingerprint metadata.

Unavailable host-specific metrics are reported as `null`; the harness does not fabricate values.

The machine-readable schema is `docs/schemas/benchmark-report.v1.schema.json`.

## Local use

Run a small deterministic benchmark:

```bash
platform-benchmark single-node \
  --operations 50 \
  --concurrency 10 \
  --warmup-operations 5 \
  --output artifacts/benchmarks/single-node.json
```

If `--data-dir` is omitted, the command uses an isolated temporary data root and removes it after the report is written. A supplied data root should be dedicated to benchmarking; do not point load tests at unrelated platform state.

For a concurrency sweep, run separate reports so each point preserves its own environment and workload metadata:

```bash
platform-benchmark single-node --operations 100 --concurrency 1 --output bench-c1.json
platform-benchmark single-node --operations 100 --concurrency 10 --output bench-c10.json
platform-benchmark single-node --operations 100 --concurrency 50 --output bench-c50.json
```

Absolute numbers from different machines must not be compared as if the hardware were identical.

## Baseline comparison

A prior report can be supplied as a comparison baseline:

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

A baseline is rejected as incomparable when the benchmark ID/version/profile/concurrency or core runtime environment fingerprint does not match.

## CI tiers

The intended tiers are:

- **PR smoke:** small deterministic workload, correctness mandatory, report uploaded as an artifact; no noisy universal latency budget.
- **integration/nightly:** larger concurrency sweeps, read/write mixes, persistence growth and comparative baselines.
- **release qualification:** stress, soak/endurance, restart/recovery under load and the supported distributed profiles.

Full soak and saturation runs do not belong in every ordinary PR.

## Progressive #440 profiles

The current foundation intentionally does not claim completion of the whole issue. Add the following as their dependencies stabilize:

- durable Plan/Step long-linear, fan-out/fan-in, retry and reconciliation workloads (#384);
- local/remote Worker dispatch, heartbeat and Workspace materialization workloads (#240/#433);
- read-heavy and mixed read/write API profiles;
- large persisted Task/Run/Event history profiles;
- bounded admission/persistence saturation stress profiles;
- longer-running memory/descriptor/queue soak profiles;
- deterministic fault-under-load profiles;
- optional HA promotion profile (#89);
- optional Memory/Knowledge/Search/Connector profiles.

Each new profile must reuse the versioned report envelope and correctness rules rather than creating an unrelated benchmark format.
