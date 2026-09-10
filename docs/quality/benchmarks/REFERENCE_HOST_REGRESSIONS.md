# Reference-host regression classification

Issue #440 requires performance results to be comparable against a compatible baseline with an explicit regression classification. This layer performs that classification only after repeated real `release` campaigns have already been summarized by `platform-reference-host-reproducibility`.

It does **not** establish platform-wide default budgets and does not turn CI smoke measurements into capacity evidence.

## Inputs

`platform-reference-host-regression` requires three files:

1. a baseline `reproducibility.json`;
2. a candidate `reproducibility.json`;
3. an explicit versioned regression-policy JSON document.

Both reproducibility inputs must have:

- `comparison_status = release-variability-observed`;
- `profile = release`;
- at least the campaign count required by the policy;
- matching host label, configuration digest, environment fingerprint, deployment profile, persistence profile and workload distribution;
- successful correctness evidence.

The platform version and immutable commit may differ because comparing releases or revisions is the purpose of this layer.

Smoke reports are rejected. A GitHub-hosted smoke run proves the comparator contract only; it cannot classify a real release performance regression.

## Policy contract

The policy schema is `docs/schemas/benchmark-reference-host-regression-policy.v1.schema.json`.

The repository intentionally ships **no default policy instance**. A policy must be authored only after real repeated measurements exist and must contain:

- a stable policy ID and version;
- a textual evidence-based justification;
- retained evidence references;
- a minimum campaign count;
- one or more metric rules;
- a minimum metric sample count per rule;
- a measured noise tolerance;
- a warning regression threshold;
- a release-blocking regression threshold.

Each rule must satisfy:

`noise_tolerance_relative < warning_relative_regression < release_blocking_relative_regression`

Rule evidence references must be declared by the parent policy. This prevents a threshold from silently losing its provenance.

Metric directions are canonical rather than policy-selectable semantics:

- throughput: higher is better;
- latency, storage/resource growth, descriptor growth and latency drift: lower is better.

The policy records the direction explicitly, and the comparator rejects a direction that conflicts with the metric semantics.

## Relative regression semantics

Positive `relative_regression` always means the candidate is worse than the baseline.

For a higher-is-better metric:

`(baseline_median - candidate_median) / abs(baseline_median)`

For a lower-is-better metric:

`(candidate_median - baseline_median) / abs(baseline_median)`

A zero baseline median is rejected because a relative regression is undefined. If a future real policy needs zero-baseline handling, that requires an explicit absolute-threshold contract rather than an arbitrary denominator.

Per-rule classifications are:

- `within-noise`: absolute change is inside the policy noise tolerance;
- `improvement`: candidate is better by more than the noise tolerance;
- `pass`: regression exceeds noise but remains below the warning threshold;
- `warning`: regression reaches the warning threshold but remains below release-blocking;
- `release-blocking`: regression reaches the release-blocking threshold.

Overall classification is the highest severity among rules: `pass`, `warning`, or `release-blocking`.

## Command

```bash
platform-reference-host-regression \
  --baseline artifacts/benchmarks/reference-linux-a/baseline/reproducibility.json \
  --candidate artifacts/benchmarks/reference-linux-a/candidate/reproducibility.json \
  --policy path/to/evidence-backed-regression-policy.json \
  --output artifacts/benchmarks/reference-linux-a/regression.json
```

The output is validated against `benchmark-reference-host-regression-report.v1.schema.json` before it is written.

## Evidence boundary

The output keeps `platform_budget_status = not-established`. The supplied policy is an evidence-scoped comparison policy for a documented reference environment; it is not a universal platform hardware requirement.

A valid regression report retains SHA-256 identities for the baseline, candidate and policy inputs together with the policy justification and evidence references.

## Relationship to #19

Issue #19 remains the owner of generic evaluation/regression quality infrastructure. The #440 comparator does not replace or fork the generic evaluation engine. It handles benchmark-specific concepts that the generic engine does not currently own, including same-reference-host comparability, repeated release-campaign evidence, metric direction and relative performance regression thresholds.

Selected resulting classifications can later be surfaced through #19 or release acceptance without making #19 responsible for load generation or benchmark methodology.

## CI boundary

The dedicated PR smoke runs formatting, linting, typing and focused contract tests. Its release-shaped JSON inputs are test fixtures only and are not uploaded or retained as performance evidence.

Real regression classification begins only after repeated fixed `release` campaigns have been executed independently on the documented reference host and a policy has been justified from retained measurements.
