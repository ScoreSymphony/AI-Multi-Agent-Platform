# Evaluation reproducibility and EvalManifest

Issue #594 hardens the existing #19 Evaluation framework. It does not introduce a second evaluation lifecycle. `EvaluationCase`, `EvaluationSuite`, `EvaluationRun`, `EvaluationAttempt`, `EvaluationResult`, aggregation and regression remain owned by #19; `EvalManifest` is immutable reproducibility evidence bound to one `EvaluationRun`.

## Goals

The reproducibility layer answers four questions for every configured evaluation:

1. What exact effective configuration, source/context inputs and environment were evaluated?
2. Which repeat and randomness policy was used, and what actually happened per repeat?
3. Are two runs directly comparable, comparable only with warnings, or incomparable for the requested claim?
4. Which dimensions were intentionally changed by the candidate and which changes are accidental drift?

Reproducibility is evidence about configuration and execution conditions. It does not claim bit-identical output from stochastic models or external systems.

## EvalManifest schema

New manifests use schema `1.1`. The manifest binds:

- exact EvaluationSuite and EvaluationCase versions;
- case fixtures, timeout and resource-limit declarations;
- platform version and commit;
- canonical version references from the effective `ConfigurationSnapshot`, including Agent/AgentTeam, planner, orchestrator, executor, model/provider configuration, routing profile, capabilities/tools, evaluators and other versioned dependencies where present;
- Skill Bundle references/digests;
- Context Bundle references/digests;
- Research Evidence and fixture/source references;
- verification-policy, contract and dependency references;
- optional canonical Workspace reference;
- repeat policy;
- seed/randomness policy;
- environment fingerprint and safe metadata;
- explicit reproducibility limitations.

Provider-private session, conversation and thread identifiers are not canonical evaluation evidence and must not be used as manifest identities.

### Digest and manifest identity

Schema `1.1` deliberately separates two identities:

- `manifest_digest` is the SHA-256 digest of the **run-independent effective reproducibility payload**. `evaluation_run_id` is excluded. Two different runs with identical effective configuration therefore have the same digest.
- `manifest_id` is unique to the manifest attached to a particular run. It combines a hash of the canonical EvaluationRun ID with a prefix of the reproducibility digest.

This distinction lets the system answer both "is this the same effective configuration?" and "which immutable evidence record belongs to this run?" without conflating those questions.

Schema `1.0` manifests remain decodable with their original digest/ID semantics. Historical evidence is not silently re-hashed under schema `1.1` rules.

## Repeat policy

`RepeatPolicy` is versioned and supports:

- `single`: exactly one run;
- `fixed_n`: execute exactly the configured repeat count;
- `stability`: execute up to `repeat_count` and stop once the documented stability criterion is met after `min_repeats`;
- `paired_ab`: record an explicit paired-comparison strategy, normally with the same ordered seed set and fixtures on baseline/candidate sides where supported.

For `stability`, the runner evaluates each `(case, evaluator)` sample stream over the configured `stability_window`. Numeric evaluator scores use population variance. Results without scores use pass/error/fail observations mapped to a deterministic 1/0 stability signal. Early stopping happens only when every expected case/evaluator stream satisfies the variance threshold.

`EvalManifest.repeat_policy.repeat_count` records the configured maximum/planned count. The completed `EvaluationRun.repetitions` records the actual executed count. Manifest projections additionally expose `actual_repeat_count` and `repeat_completion` (`completed_planned_repeats`, `stability_reached`, or equivalent projection state).

Every raw `EvaluationResult` remains persisted with its `repetition_index`, `attempt_id` and resolved seed. No repeat is replaced by an unexplained average.

## Seed and randomness policy

`SeedPolicy` records an explicit `RandomnessMode`:

- `deterministic_no_randomness`;
- `fixed_seed_supported`;
- `fixed_seed_requested_provider_unsupported`;
- `provider_managed_stochastic`;
- `external_nondeterminism_present`;
- `seed_support_unknown` for legacy or undeclared cases.

Fixed-seed policies preserve the ordered seed set. `fixed_seed_supported` requires explicit evidence that the provider/component accepts seed control. `fixed_seed_requested_provider_unsupported` preserves requested seeds but records that provider seed control is false; it must not fabricate determinism.

The legacy `EvaluationRun.seed` input remains supported. It is treated conservatively: derived values can be recorded for attempts, but the existence of a legacy seed does not prove provider seed support.

## Repeat statistics

Read projections retain raw per-repeat outcomes and also provide explanatory statistics grouped by `(case_id, case_version, evaluator_id)`:

- sample count;
- pass rate;
- mean score when all samples expose scores;
- population score variance when all samples expose scores.

These statistics are derived evidence only. Raw `EvaluationResult` records remain the source evidence, and regression semantics continue to require an explicit `AggregationPolicy` when repeated samples are compared through the #19 regression engine.

## Manifest comparison

`ManifestComparator` returns one of:

- `directly_comparable`;
- `comparable_with_warnings`;
- `incomparable`;
- `unknown`.

It also emits field-level `ManifestDifference` records containing baseline value, candidate value, blocking classification and whether the difference is an intentional candidate dimension.

The comparison is strict for execution-defining evidence such as:

- suite/case revision;
- platform version/commit unless `platform` is explicitly the candidate dimension;
- model/provider/planner/orchestrator/executor/capability references;
- Skill and Context Bundle references;
- source/fixture revisions;
- repeat/seed policy;
- Workspace and other bound canonical references.

Environment differences are warnings for quality comparisons by default. Hardware/resource-related environment drift becomes blocking when `performance_sensitive=True`.

`regression_policy` and `aggregation_policy` references are comparison/interpretation lenses. They remain visible evidence but do not by themselves make the underlying evaluated execution configurations incompatible.

### Intentional candidate dimensions

Callers can declare `candidate_reference_kinds`, for example:

- `planner` for Planner A/B;
- `model` or a model-configuration reference kind;
- `skill_bundle`;
- `context_bundle`;
- repository-intelligence provider reference kinds;
- `platform` for an intentional platform version/commit candidate.

A declared candidate difference is non-blocking. All other dimensions remain strict, so accidental Skill/Context/source/configuration drift still invalidates a regression/improvement claim.

Paired A/B evaluations should use the same repeat policy, fixture/source evidence and ordered seed set whenever the evaluated components expose meaningful seed control.

## Regression integration

`EvaluationRunner` and `EvaluationService.compare_runs()` gate #19 regression/improvement claims on manifest compatibility first.

- `unknown`: a comparison claim is rejected because canonical manifest evidence is missing.
- `incomparable`: the claim is rejected and blocking fields are reported.
- `directly_comparable` / `comparable_with_warnings`: the normal #19 regression engine may proceed.

Repeated comparisons still require an explicit `AggregationPolicy`. If that policy requires equal sample counts, the runner checks the **actual completed repetition count**, including after stability-based early stopping.

## Persistence

`SqliteEvalManifestRepository` stores one immutable manifest per `EvaluationRun` in the same SQLite deployment boundary used by single-node Evaluation. Saving a different manifest for the same run is a conflict. Manifest/result links survive process restart.

Changing later platform configuration does not mutate historical manifests. Imported/copy-preserved manifests may describe historical evidence but do not imply that the current target environment can exactly reproduce the original run.

## Control Plane, CLI and Web

The existing Evaluation surface remains canonical:

- `GET /api/v1/evaluation-runs/{run_id}` includes the read-only manifest projection with ID/digest, repeat/seed policy, environment fingerprint, raw per-repeat outcomes, repeat statistics and limitations.
- `evaluation.run` accepts optional explicit `repeat_policy`, `seed_policy`, `candidate_reference_kinds` and `performance_sensitive` fields in addition to existing snapshot/repetition/baseline inputs.
- `evaluation.compare` returns the normal `ComparisonReport` plus `manifest_comparison` with status and field-level differences.
- CLI `eval result show` inspects the complete persisted run/manifest projection.
- CLI `eval run` accepts `--repeat-policy-json`, `--seed-policy-json`, repeatable `--candidate-reference-kind` and `--performance-sensitive`.
- CLI `eval compare` accepts the same candidate-dimension/performance classification controls and returns the manifest diff projected by the Control Plane.
- The Web Evaluation run detail renders the persisted manifest, repeat statistics, per-repeat evidence and reproducibility limitations. A comparison response renders manifest compatibility and exact changed fields alongside the regression findings.

No CLI or Web surface owns provider sessions, evaluator internals or a second evaluation configuration lifecycle.

## Deterministic no-paid CI profile

The existing reference deterministic CI profile remains no-paid-service capable. It can produce canonical manifests using local/reference components and does not require a model judge or external provider.

This #594 hardening does not change the ownership of CI or performance methodology. #440 remains responsible for performance benchmarks; the manifest only classifies environment/resource drift when performance-sensitive comparison is requested.

## Compatibility and migration notes

- `EvalManifest` schema `1.0` remains readable.
- New manifests are emitted as schema `1.1`.
- Existing #19 `EvaluationRun`/`EvaluationResult` persistence remains canonical.
- Legacy `seed` inputs remain accepted but are explicitly classified as insufficient proof of provider seed control.
- Comparison-policy references remain visible evidence but are not treated as candidate execution drift.
- No provider-private session ID becomes a canonical identifier as part of this migration.
