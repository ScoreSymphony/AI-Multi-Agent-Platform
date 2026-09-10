# Reference-host absolute performance budgets

Issue #440 requires performance regression policy to support absolute thresholds where they are justified by evidence, in addition to the relative baseline comparison provided by `platform-reference-host-regression`.

This layer evaluates one repeated real `release` reproducibility report against an explicit, versioned absolute budget policy for the same documented reference environment. It does **not** establish universal platform hardware requirements and does not derive thresholds from GitHub-hosted smoke measurements.

## Relationship to relative regression classification

The two tools answer different questions:

- `platform-reference-host-regression` compares a baseline release report with a candidate release report and classifies relative change;
- `platform-reference-host-absolute-budget` evaluates one release report against explicit absolute boundaries.

The absolute-budget contract is separate rather than extending the relative-policy v1 schema retroactively. Existing relative policies therefore keep their original meaning.

## Evidence requirements

`platform-reference-host-absolute-budget` requires:

1. one `reproducibility.json` produced from repeated real reference-host campaigns;
2. one explicit absolute-budget policy JSON document;
3. one output path distinct from both evidence inputs.

The reproducibility report must have:

- `comparison_status = release-variability-observed`;
- `profile = release`;
- successful correctness evidence;
- at least the campaign count required by the policy;
- at least the metric sample count required by every evaluated rule.

Smoke evidence is rejected. CI release-shaped fixtures validate the contract only and are not capacity or budget evidence.

## Reference-environment scope

The policy binds absolute numbers to the exact reference basis that justified them. The following fields must match the release evidence exactly:

- host label;
- fixed release configuration digest;
- environment fingerprint;
- deployment profile;
- persistence profile;
- workload distribution.

The platform version and immutable platform commit are retained in the evaluated evidence but are not fixed by the policy. This allows the same evidence-backed budget to evaluate later releases on the same reference environment.

A policy from one host must not be silently applied to another host or to an environment whose fingerprint changed.

## Policy contract

The policy schema is `docs/schemas/benchmark-reference-host-absolute-budget-policy.v1.schema.json`.

The repository intentionally ships **no policy instance and no default numbers**. A policy must be authored from retained real measurements and contains:

- stable policy ID and version;
- evidence-based justification;
- retained evidence references;
- minimum campaign count;
- exact reference-environment basis;
- one or more metric rules;
- minimum metric sample count per rule;
- warning boundary;
- release-blocking boundary.

Rule evidence references must also be declared by the parent policy, so an individual threshold cannot silently lose provenance.

Metric directions are canonical:

- throughput: higher is better;
- latency, storage/resource growth, descriptor growth and latency drift: lower is better.

The evaluator rejects a policy that declares the wrong direction.

## Boundary ordering and classification

For a higher-is-better metric, such as throughput:

```text
release_blocking_boundary < warning_boundary
```

The result is:

- `release-blocking` when the observed median is less than or equal to the release-blocking boundary;
- `warning` when it is above the blocking boundary but less than or equal to the warning boundary;
- `pass` when it is above the warning boundary.

For a lower-is-better metric, such as p95 latency:

```text
warning_boundary < release_blocking_boundary
```

The result is:

- `release-blocking` when the observed median is greater than or equal to the release-blocking boundary;
- `warning` when it is below the blocking boundary but greater than or equal to the warning boundary;
- `pass` when it is below the warning boundary.

Boundary equality is intentionally inclusive. Overall classification is the highest severity among all rules.

## Command

```bash
platform-reference-host-absolute-budget \
  --evidence artifacts/benchmarks/reference-linux-a/reproducibility.json \
  --policy path/to/evidence-backed-absolute-budget-policy.json \
  --output artifacts/benchmarks/reference-linux-a/absolute-budget.json
```

The CLI rejects an output path that aliases either evidence input directly, through a symlink, or through a hard link. The generated report is validated against `benchmark-reference-host-absolute-budget-report.v1.schema.json` before being written.

## Result semantics

The report deliberately records:

```text
claim_semantics = "evidence-scoped-absolute-budget-policy-only"
platform_budget_status = "not-established"
budget_scope = "reference-environment-only"
```

`platform_budget_status` remains `not-established` because a host-scoped absolute policy is not a universal platform budget. The report retains the policy SHA-256, release-evidence SHA-256, policy justification, evidence references, platform commit/version, verified basis, observed medians, sample counts and classifications.

## Establishing real policies

A real policy should only be introduced after enough independent fixed `release` campaigns exist on the documented reference host to justify both the sample requirements and the numeric boundaries. The retained measurement evidence must remain reviewable.

This tool validates and applies such a policy; it does not invent boundaries or automatically promote observed values into release requirements.

If future evidence supports a genuinely hardware-independent absolute requirement, that should be introduced through a separately justified contract. One reference host must not be generalized into a universal capacity claim.

## CI boundary

The dedicated PR smoke runs formatting, linting, typing and focused contract tests. It also verifies that this feature does not ship a built-in policy instance. No CI fixture is retained or presented as measured reference-host capacity evidence.
