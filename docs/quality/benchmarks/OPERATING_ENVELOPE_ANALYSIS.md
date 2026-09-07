# Operating Envelope Analysis

Issue #440 already provides deterministic single-node sweep and endurance harnesses. This
analysis layer turns those existing machine-readable reports into a comparable **tested
operating envelope** without generating new load and without inventing universal hardware
requirements.

## Scope

`platform-operating-envelope` accepts:

- one or more `single-node.reference.lifecycle.sweep` summary reports;
- zero or more `single-node.reference.endurance` reports with `scenario=soak`.

The analyzer rejects evidence unless all accepted reports share the same:

- platform version;
- exact platform commit;
- deployment profile;
- persistence profile;
- environment metadata;
- sweep workload/configuration and concurrency levels.

Every sweep point and soak report must also have passed its existing correctness checks.
Incomplete sweep repetition sets are rejected.

## What the report means

The output records:

- a SHA-256 fingerprint of the exact environment metadata;
- min/median/max throughput for each tested concurrency level;
- min/median/max p95 latency for each tested concurrency level;
- sample counts and median duration/storage growth per concurrency level;
- the highest concurrency level for which complete comparable evidence was supplied;
- optional soak duration, resource growth, latency drift and completion evidence.

`highest_verified_concurrency` means **the highest level actually tested in the supplied
complete evidence set**. It is not a discovered saturation point and not a universal capacity
claim.

The v1 report deliberately emits:

```text
claim_semantics = "tested-envelope-only"
budget_status = "not-established"
```

Performance budgets should only be introduced after comparable measurements exist across the
reference environments/releases that the project actually intends to support.

## Example release-sized evidence collection

Generate repeated sweep evidence with the existing bounded harness:

```bash
platform-benchmark single-node-sweep \
  --concurrency-levels 1,10,50,100 \
  --operations-per-level 500 \
  --warmup-operations 20 \
  --repetitions 5 \
  --timeout-seconds 60 \
  --platform-commit "$(git rev-parse HEAD)" \
  --output-dir artifacts/benchmarks/release-sweep
```

Generate a longer bounded soak report:

```bash
platform-benchmark single-node-endurance \
  --scenario soak \
  --duration-seconds 3600 \
  --sample-interval-seconds 10 \
  --max-operations 20000 \
  --concurrency 10 \
  --seed-tasks 100 \
  --warmup-operations 20 \
  --timeout-seconds 60 \
  --platform-commit "$(git rev-parse HEAD)" \
  --output artifacts/benchmarks/release-soak.json
```

Then derive the tested envelope:

```bash
platform-operating-envelope \
  --sweep artifacts/benchmarks/release-sweep/summary.json \
  --endurance artifacts/benchmarks/release-soak.json \
  --output artifacts/benchmarks/operating-envelope.json
```

Additional comparable sweep or soak reports may be supplied by repeating `--sweep` or
`--endurance`.

## Safety boundary

This analyzer is read-only with respect to benchmark evidence. It does not create Tasks, start
Workers, generate memory pressure, change cgroups, alter swap/zRAM/kernel settings, or run
stress workloads itself.

Release-sized runs remain bounded by the safety controls of the underlying benchmark harnesses.
Dedicated-host paging/OOM experiments remain a separate #440 follow-up and must not be inferred
from this report.

## Schema

The machine-readable output is validated by:

`docs/schemas/benchmark-operating-envelope.v1.schema.json`
