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

The workload report records a versioned scenario/configuration, persistence and deployment profiles, workload distribution, throughput and p50/p95/p99 latency distributions, resource evidence, observed canonical totals, duplicate-write detection, bounded canonical samples, errors and a correctness verdict. Its schema is `docs/schemas/benchmark-workload.v1.schema.json`.

## Idle footprint and soak/endurance

`platform-benchmark single-node-endurance` adds two single-process profiles that answer questions the request/operation reports cannot answer reliably:

- `idle` constructs and initializes the production-shaped single-node deployment, creates the benchmark administrator/project context, executes no Task/Run workload, and samples CPU, Python traced memory, optional peak RSS, storage and open descriptors for the configured duration;
- `soak` keeps one deployment instance alive, seeds canonical state before measurement, optionally warms read paths, then performs a deterministic bounded read/write mix while periodically sampling resources and windowed latency.

Idle example:

```bash
platform-benchmark single-node-endurance \
  --scenario idle \
  --duration-seconds 60 \
  --sample-interval-seconds 5 \
  --output artifacts/benchmarks/idle.json
```

Longer soak example:

```bash
platform-benchmark single-node-endurance \
  --scenario soak \
  --duration-seconds 3600 \
  --sample-interval-seconds 30 \
  --max-operations 100000 \
  --concurrency 8 \
  --seed-tasks 100 \
  --warmup-operations 20 \
  --read-weight 4 \
  --write-weight 1 \
  --output artifacts/benchmarks/soak.json
```

A soak always has two independent bounds: requested duration and `max_operations`. The first bound reached stops the workload. The operation cap is a harness-safety boundary, not a claimed platform capacity. To run a true duration-dominated soak, choose a sufficiently high operation cap for the target environment.

Each report stores startup latency, aggregate operation/read/write p50/p95/p99, throughput, initial/final resource evidence and periodic snapshots. Snapshot latency is windowed since the previous sample, allowing latency drift to be inspected instead of hiding it inside one cumulative percentile. The report also records traced-memory growth, optional peak-RSS growth, optional descriptor growth, storage growth and a first-to-last window p95 drift ratio when at least two populated windows exist.

Correctness remains mandatory. Idle fails if canonical Tasks or Runs appear. Soak fails on operation errors, missing canonical state or duplicate write identities. The schema is `docs/schemas/benchmark-endurance.v1.schema.json`.

## Controlled saturation stress

`platform-benchmark single-node-stress` escalates the deterministic canonical Task -> Run lifecycle through explicitly requested concurrency levels. It is intentionally different from the ordinary sweep: stress has hard harness-safety limits and may stop escalation immediately when correctness fails.

```bash
platform-benchmark single-node-stress \
  --concurrency-levels 10,25,50,100 \
  --operations-per-level 100 \
  --warmup-operations 5 \
  --safety-max-concurrency 128 \
  --safety-max-operations-per-level 500 \
  --output-dir artifacts/benchmarks/stress
```

Every stress point uses a fresh data root and the authenticated deterministic lifecycle path. The summary records throughput, p50/p95/p99 latency, error rate, process-memory evidence, storage growth and correctness for every completed level. Requested levels must be unique and strictly increasing.

`--safety-max-concurrency` and `--safety-max-operations-per-level` are host-protection boundaries. A request exceeding them is rejected before the benchmark executes. They are not platform capacity declarations. Likewise, completing a high concurrency point is measured evidence for that environment, not a universal support claim.

By default, canonical correctness failure stops further escalation and records the first failed concurrency. `--continue-after-correctness-failure` is available for an explicitly controlled diagnostic run, but the overall stress report remains failed. The summary schema is `docs/schemas/benchmark-stress.v1.schema.json`; each point also retains the ordinary `benchmark-report.v1` lifecycle evidence.

## Fault under load

`platform-benchmark single-node-fault-under-load` currently provides one deterministic fault profile: `control-plane-restart`. It runs bounded authenticated read/write load, reconstructs the real `SingleNodeDeployment` on the same durable state at a configured operation boundary, then continues the measured load.

```bash
platform-benchmark single-node-fault-under-load \
  --operations 100 \
  --concurrency 8 \
  --fault-after-operations 50 \
  --seed-tasks 20 \
  --warmup-operations 5 \
  --safety-max-operations 500 \
  --safety-max-concurrency 16 \
  --read-weight 4 \
  --write-weight 1 \
  --output artifacts/benchmarks/restart-under-load.json
```

The fault boundary is required to split the workload: there must be measured operations both before and after the restart. The report measures restart latency plus aggregate operation/read/write latency and resource evidence. Correctness additionally requires:

- durable bearer authentication still resolves to the same canonical actor after restart;
- an unauthenticated canonical Task request remains blocked;
- `/health` and `/readiness` are reachable again after reconstruction;
- both pre-fault and post-fault load complete;
- seeded and newly written canonical Tasks/Runs remain present;
- write identities are not duplicated.

The operation and concurrency safety limits are explicit harness boundaries, not advertised platform capacity. The schema is `docs/schemas/benchmark-fault-under-load.v1.schema.json`.

This first fault profile deliberately uses a real supported recovery seam rather than a private repository failure hook. Transport outage/backpressure, transient persistence failure, Worker disconnect/reconnect, provider unavailability and HA promotion require their own stable integration seams and remain progressive profiles.

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

- **PR smoke:** small deterministic lifecycle, tiny 1/2 sweep, tiny workload fixtures, contract-sized idle/soak, 1/2 bounded stress and a six-operation restart-under-load fixture; correctness and schemas mandatory, artifacts retained; no noisy universal latency budget.
- **integration/nightly/manual performance work:** full 1/10/50/100+ sweeps, realistic read/mixed/history/restart sizes, longer resource-sampling windows, larger controlled stress sweeps, restart-under-load runs and comparative baselines.
- **release qualification:** meaningful soak/endurance durations, controlled saturation to an explicitly chosen host boundary, fault-under-load and supported distributed profiles.

Tiny endurance/stress/fault runs in PRs prove semantics only. They are not evidence of long-term memory stability or a production operating envelope. Full scale, soak and saturation runs do not belong in every ordinary PR.

## Progressive #440 profiles

The suite still does not claim completion of #440. Remaining progressive work includes:

- transient persistence failure under moderate load where a stable provider-level fixture is available;
- transport outage/backpressure and duplicate delivery under load;
- model/tool/provider unavailability fixture under load;
- durable Plan/Step long-linear, fan-out/fan-in, retry and reconciliation workloads after #384;
- local/remote Worker dispatch, heartbeat, Worker loss/rejoin and Workspace materialization workloads after #240/#433;
- optional HA promotion profile (#89);
- optional Memory/Knowledge/Search/Connector profiles.

Each new profile must preserve the versioned evidence model and correctness rules rather than creating an unrelated benchmark methodology.
