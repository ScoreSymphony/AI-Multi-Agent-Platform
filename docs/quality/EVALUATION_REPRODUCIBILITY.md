# Evaluation reproducibility and EvalManifest

Issue #594 hardens the canonical Evaluation framework owned by #19. It does **not** create a second evaluation lifecycle. `EvaluationCase`, `EvaluationSuite`, `EvaluationRun`, `EvaluationAttempt`, `EvaluationResult`, aggregation and regression remain #19-owned; `EvalManifest` is immutable reproducibility evidence bound to an `EvaluationRun`.

## Goals

An evaluation run should be able to answer, after restart and after later configuration changes:

- which exact suite/case revisions were evaluated;
- which effective platform/configuration references were active;
- which Skill, Context, research, fixture/source, dependency and contract revisions were bound where applicable;
- how many repeats were planned and actually executed;
- which randomness/seed controls were available and used;
- which environment fingerprint was observed;
- whether a baseline/candidate comparison is directly comparable, warning-only, incomparable or unknown;
- which fields differ and which differences are intentional candidate dimensions;
- which raw per-repeat outcomes produced an aggregate or comparison claim.

Provider-private session, conversation or thread identifiers are not canonical reproducibility identity.

## EvalManifest schema

The current manifest schema is `1.1`.

Schema `1.1` separates two identities that intentionally have different semantics:

- `manifest_digest` is a deterministic digest of the reproducibility payload. It excludes the dynamic `evaluation_run_id`, so two runs with the same effective reproducibility configuration have the same digest.
- `manifest_id` is unique per EvaluationRun. It combines run-bound identity with the reproducibility digest, so multiple runs with identical configuration can coexist in durable storage.

This distinction is required for both stable configuration comparison and one immutable manifest row per run.

### Schema 1.0 compatibility

Previously persisted schema `1.0` manifests remain decodable. Their historical digest/ID calculation included the run identity. Decoding validates those records according to the `1.0` rule instead of silently rewriting historical evidence to `1.1` semantics.

New manifests use `1.1`; historical `1.0` manifests remain evidence of what was recorded at the time.

## Bound evidence

An EvalManifest contains or derives at least:

- manifest schema version;
- EvaluationSuite identity/version;
- exact case identities/versions;
- case fixture references, timeout and resource limits;
- platform version and commit;
- versioned configuration references from `ConfigurationSnapshot`;
- repeat policy;
- seed/randomness policy;
- environment fingerprint;
- optional SkillBundle references;
- optional ContextBundle references;
- optional Research Evidence references;
- optional fixture/source revisions;
- optional verification-policy references;
- optional dependency/plugin/upstream references;
- optional contract-version references;
- optional canonical workspace reference;
- explicit reproducibility limitations.

Reference ordering and environment ordering are canonicalized before hashing. Changing a significant revision therefore changes the digest; merely reordering equivalent references does not.

## Repeat policy

`RepeatPolicy` supports these strategies:

- `single`: exactly one deterministic/logical repetition;
- `fixed_n`: execute exactly the configured repeat count;
- `stability`: execute up to the configured maximum and stop after a deterministic stability criterion is satisfied;
- `paired_ab`: use an explicitly shared repeat/seed policy for A/B candidate comparisons.

For `stability`, the manifest records the configured maximum (`repeat_count`), minimum repeat count, stability window and variance threshold. The completed `EvaluationRun.repetitions` records the **actual** executed count after early stopping. The manifest projection reports both the planned maximum and actual repeat count plus `repeat_completion`.

Raw results are never replaced by an unexplained average. Every `EvaluationResult` retains `repetition_index`, `attempt_id` and the effective seed where one exists.

## Stability semantics

The reference stability rule is deterministic:

1. execute at least `min_repeats`;
2. require at least `stability_window` completed repetitions;
3. group observed evaluator scores by case/evaluator over the trailing stability window;
4. require complete numeric score evidence for the window;
5. compute population variance for each group;
6. stop only if every group has variance `<= variance_threshold`.

If the evidence cannot satisfy that rule, execution continues until the configured maximum. Deterministic pass/fail results still remain preserved as raw evidence; score-based stability does not fabricate numeric values for evaluators that do not emit scores.

## Seed and randomness policy

`SeedPolicy` explicitly distinguishes:

- `deterministic_no_randomness`;
- `fixed_seed_supported`;
- `fixed_seed_requested_provider_unsupported`;
- `provider_managed_stochastic`;
- `external_nondeterminism_present`;
- `seed_support_unknown` for conservative legacy evidence.

A fixed supported seed policy requires one ordered seed per planned repetition and `provider_seed_control=true`. A fixed requested-but-unsupported policy preserves the requested seed set but records `provider_seed_control=false`; this is evidence of an unsuccessful control attempt, not a claim that the provider honored the seeds.

The legacy `EvaluationRun.seed` input remains supported for compatibility. It is mutually exclusive with an explicit `SeedPolicy`. A legacy seed alone does not prove provider seed support and is therefore represented conservatively in manifest evidence.

## Environment fingerprint and comparability

`EnvironmentFingerprint` hashes sorted non-secret environment metadata. It is comparison evidence, not deployment identity.

`ManifestComparator` produces one of:

- `directly_comparable`;
- `comparable_with_warnings`;
- `incomparable`;
- `unknown` when one side has no manifest.

The comparator emits field-level differences with:

- `path`;
- baseline value;
- candidate value;
- `blocking` classification;
- `intentional_candidate_dimension` classification.

### Blocking dimensions

By default, drift in execution-relevant dimensions blocks regression/improvement claims, including suite/case revision, platform revision, model/provider/config references, Skill/Context/source evidence and other manifest references.

### Intentional candidate dimensions

Callers may declare reference kinds that are the intended candidate variable, for example:

- `model`;
- `planner`;
- `skill_bundle`;
- `context_bundle`;
- provider/repository-intelligence reference kinds;
- `platform` when intentionally comparing platform revisions.

Those differences remain visible in the diff but do not block comparison. Non-candidate drift is still rejected.

### Comparison-lens references

`regression_policy` and `aggregation_policy` are comparison/interpretation lenses rather than candidate execution dimensions. Their presence or version drift is reported as warning evidence instead of making otherwise identical evaluated configurations incomparable.

### Performance-sensitive comparisons

OS/runtime/environment drift is normally warning evidence. When `performance_sensitive=true`, relevant hardware/resource changes such as CPU, GPU, RAM/memory or resource profile become blocking because latency/performance claims are not meaningful across materially different resource profiles.

Performance benchmark methodology remains #440-owned; EvalManifest only supplies compatible environment/comparability evidence.

## Comparison and regression gating

Regression or improvement claims are permitted only after manifest compatibility is known.

- `unknown`: no comparison claim;
- `incomparable`: no comparison claim;
- `directly_comparable` or warning-only: regression policy may evaluate the canonical results/aggregates.

The normal `RegressionEngine` remains responsible for regression semantics. EvalManifest is a gate and evidence layer, not a parallel regression implementation.

Repeated runs continue to require an explicit `AggregationPolicy` when an aggregate comparison is requested. Aggregation retains source result IDs, repetition indices, seeds and outcomes.

## Projection and reporting

The canonical read projection exposes:

- manifest ID and digest;
- schema version;
- repeat policy;
- actual repeat count and completion reason;
- seed/randomness policy;
- environment digest and optional comparability classification;
- raw per-repeat outcomes;
- repeat statistics by case/evaluator;
- reproducibility limitations;
- manifest diff when a comparison is available.

Repeat statistics include sample count, pass rate and, when numeric score evidence exists, mean and population variance. They are explanatory evidence and do not silently replace the configured aggregation/regression policy.

## Persistence

`SqliteEvalManifestRepository` stores one immutable manifest per `EvaluationRun` in the same durable SQLite database used by the single-node Evaluation composition. Result persistence already preserves repetition index and seed, so manifest/result links survive restart.

Saving a different manifest for an existing run is a conflict. Re-saving identical immutable evidence is idempotent.

Imported/historical manifests may be retained as evidence, but their presence does not claim that a target environment can reproduce the original run bit-for-bit.

## Control Plane

Existing Evaluation resources and commands remain canonical:

- `evaluation-suites`;
- `evaluation-runs`;
- `evaluation.run`;
- `evaluation.compare`.

`evaluation-runs/{run_id}` exposes the read-only manifest projection when present.

`evaluation.run` additionally accepts optional:

- `repeat_policy`;
- `seed_policy`;
- `candidate_reference_kinds`;
- `performance_sensitive`;
- the existing aggregation/regression references where applicable.

`evaluation.compare` returns the normal comparison report plus `manifest_comparison` with status and exact field differences.

No provider-private lifecycle/session identity is accepted as canonical manifest evidence.

## CLI

The API-first CLI forwards the same canonical controls:

```text
ai-multi-agent-platform eval run <suite@version> \
  --snapshot-json '{...}' \
  --repetitions 3 \
  --repeat-policy-json '{...}' \
  --seed-policy-json '{...}' \
  --candidate-reference-kind model \
  --performance-sensitive
```

For comparisons:

```text
ai-multi-agent-platform eval compare <current-run> \
  --baseline-run-id <baseline-run> \
  --regression-policy-ref <policy@version> \
  --aggregation-policy-ref <policy@version> \
  --candidate-reference-kind skill_bundle \
  --performance-sensitive
```

`--seed` and `--seed-policy-json` are mutually exclusive. Candidate reference kinds may be supplied repeatedly and must be unique.

## Web surface

The existing Evaluations UI remains a projection of canonical Control Plane data. Run detail includes a read-only **Reproducibility manifest** section showing:

- manifest ID/schema/digest;
- planned versus actual repeats;
- randomness mode and ordered seeds;
- environment fingerprint/comparability;
- explicit limitations;
- repeat statistics;
- per-repeat outcomes.

Regression comparison detail surfaces manifest comparability and the exact baseline/candidate manifest differences. No second configuration lifecycle or provider session UI is introduced.

## Deterministic no-paid reference CI

The existing reference Evaluation CI remains compatible with the project constraint that mandatory baseline evaluation must not require paid model/API services. It uses local/reference orchestrator/executor/evaluators and can create canonical manifest evidence without a paid judge.

CI execution is operationally separate from the #594 implementation branch. When multiple active branches are consolidated, the integration branch is the appropriate place to run the unified required-check matrix.

## Historical and stochastic limitations

Reproducibility does not mean claiming bit-identical stochastic LLM behavior. A manifest proves the recorded effective configuration and controls. Provider-managed randomness, unavailable seed support, external services and environment differences remain explicit limitations.

For historical runs created before manifests existed, comparability is `unknown` unless trustworthy canonical evidence is deliberately reconstructed by a migration/import path. The runtime does not fabricate missing provenance merely to permit a comparison.
