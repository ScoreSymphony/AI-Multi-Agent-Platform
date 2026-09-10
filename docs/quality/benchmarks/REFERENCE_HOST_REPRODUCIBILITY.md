# Reference-host reproducibility analysis

Issue #440 requires performance evidence that is repeatable enough for trend/regression analysis without pretending heterogeneous hardware produces identical absolute numbers. The reference-host campaign runner provides one independently hashed host-local measurement. This reproducibility layer compares repeated campaigns from the **same host/environment basis** before any performance budget is proposed.

## What this layer proves

`platform-reference-host-reproducibility` consumes one or more completed reference-host campaign directories and validates:

- each `campaign.json` against the packaged v1 campaign schema;
- the campaign configuration digest;
- the environment fingerprint;
- the referenced operating-envelope path and SHA-256 digest;
- the operating-envelope schema and tested-envelope invariants;
- agreement between campaign and envelope commit/environment/configuration dimensions;
- same host label, platform version/commit, profile, configuration digest, environment fingerprint, deployment profile, persistence profile and workload distribution across all runs;
- unique campaign directories and unique campaign manifests so copying one result does not count as another repetition.

Only after those checks does it summarize observed variability.

## Metrics

For each tested concurrency level the report aggregates the per-campaign envelope medians for:

- throughput;
- p95 latency;
- storage growth.

For the campaign soak evidence it aggregates:

- throughput;
- p95 latency;
- traced-memory growth;
- peak RSS growth when available;
- open-file-descriptor growth when available;
- storage growth;
- latency drift when available.

Each metric records sample count, minimum, median, maximum, relative range and coefficient of variation. Relative range is `(max - min) / abs(median)` when the median is non-zero. Coefficient of variation uses population standard deviation divided by the absolute mean and is omitted when fewer than two samples exist or the mean is zero.

These are **observations**, not pass/fail thresholds.

## Evidence semantics

The report deliberately keeps:

- `claim_semantics = same-host-comparable-campaigns-only`;
- `budget_status = not-established`;
- `stability_classification = not-performed`.

The analyzer does not choose a universal sample-count threshold, noise budget or stable/unstable boundary. Those policies must be justified from real release-sized measurements rather than introduced by CI or by arbitrary constants.

A single release campaign receives `single-release-campaign`. Two or more comparable release campaigns receive `release-variability-observed`. Smoke campaigns always receive `smoke-contract-only`, regardless of count.

## Release workflow

Run the fixed release campaign independently multiple times on the same documented reference host and same exact platform commit. Each run needs a fresh output directory and a fresh explicit measured work directory:

```bash
platform-reference-host-campaign \
  --profile release \
  --host-label reference-linux-a \
  --platform-commit "$(git rev-parse HEAD)" \
  --work-dir /measured-storage/benchmark-work/run-1 \
  --output-dir artifacts/benchmarks/reference-linux-a/run-1

platform-reference-host-campaign \
  --profile release \
  --host-label reference-linux-a \
  --platform-commit "$(git rev-parse HEAD)" \
  --work-dir /measured-storage/benchmark-work/run-2 \
  --output-dir artifacts/benchmarks/reference-linux-a/run-2
```

Then analyze the run set:

```bash
platform-reference-host-reproducibility \
  --campaign-dir artifacts/benchmarks/reference-linux-a/run-1 \
  --campaign-dir artifacts/benchmarks/reference-linux-a/run-2 \
  --output artifacts/benchmarks/reference-linux-a/reproducibility.json
```

Additional independent campaigns can be supplied by repeating `--campaign-dir`.

## Relationship to cross-host evidence

Same-host reproducibility and cross-host cataloging answer different questions:

1. `platform-reference-host-campaign` measures one host-local run.
2. `platform-reference-host-reproducibility` characterizes run-to-run noise for a comparable series on one host/environment.
3. `platform-operating-envelope-catalog` catalogs independently tested envelopes from different host environments without averaging their absolute throughput or latency.

Do not feed heterogeneous host runs into the reproducibility analyzer. Their environment fingerprints are expected to differ and the analyzer rejects them.

## CI boundary

The PR workflow creates two tiny `smoke` campaigns on one GitHub-hosted runner and runs the analyzer over them. That verifies schema handling, hash chaining, same-host comparability and variability calculations only.

It is **not** release-sized capacity evidence, does not establish a stability classification and does not establish performance/noise budgets.

## Next evidence step for #440

After this tooling is merged, the remaining evidence step is operational rather than synthetic: execute repeated fixed `release` campaigns on documented real reference hosts, preserve their artifacts, inspect the observed variability, and only then define versioned regression/noise budgets that the measured distributions can justify.
