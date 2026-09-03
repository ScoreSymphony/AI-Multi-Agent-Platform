# Evaluation and Regression Framework

Issue #19 owns the platform-level evaluation and regression layer. The framework is provider-neutral, orchestrator-neutral and evaluator-neutral. Unit, integration and contract tests remain separate concerns; evaluations measure repeatable platform, agent, model, tool and workflow behavior across versioned scenarios.

## Canonical ownership

The platform owns these concepts:

- `EvaluationCase`: one versioned scenario with explicit inputs, fixtures, deterministic assertions, metric thresholds, optional rubric criteria, tags and resource limits.
- `EvaluationSuite`: a versioned collection of cases.
- `ConfigurationSnapshot`: immutable version references for the platform build and the participating agent, model, provider, orchestrator, executor, prompt, capability, policy and environment identities.
- `EvaluationRun`: one execution of a suite/configuration snapshot with repetitions, optional seed and optional accepted baseline run.
- `EvaluationAttempt`: one specific case repetition, with stable attempt identity, repetition index and resolved seed.
- `EvaluationResult`: one evaluator's result for one case attempt, including pass/fail/error, optional score, assertion evidence, metrics and canonical task/run/artifact/telemetry references.
- `RegressionPolicy`: versioned regression rules and thresholds.
- `ComparisonReport`: regressions and improvements discovered against a baseline.

Backend-private model names, evaluator APIs and orchestration/execution IDs are never canonical evaluation identities.

## Evaluator boundary

`Evaluator` is the replaceable evaluator contract. The initial reference implementation contains:

1. `DeterministicAssertionEvaluator`, which evaluates structured observations without an LLM or external service.
2. `MetricThresholdEvaluator`, which evaluates declared numeric thresholds.
3. `SafeEvaluator`, which converts evaluator failures into an explicit canonical `EvaluationResult(outcome="error")` instead of crashing the whole evaluation run.

`EvaluatorDescriptor` records evaluator identity, kind and version. A model-based evaluator must additionally record the canonical evaluator model configuration and provider identity and cannot claim deterministic behavior. Model judging is therefore an optional adapter, not ground truth and not a CI dependency.

Rubric criteria are canonical case data. A later rubric/model evaluator adapter consumes those criteria through the same `Evaluator` boundary.

## Runner and execution boundary

`EvaluationRunner` owns suite-level evaluation execution without owning platform Task/Run lifecycle semantics. It consumes two replaceable boundaries:

- `EvaluationCaseExecutor`, which executes one case attempt and returns an `EvaluationObservation`;
- `EvaluationIsolation`, which provides explicit `reset_case -> setup_case -> execute -> teardown_case` isolation around every attempt.

The runner:

- persists a running `EvaluationRun` before executing cases;
- expands every case into the requested number of repetitions;
- derives repetition seeds deterministically as `base_seed + repetition_index` when a base seed is supplied;
- creates an `EvaluationAttempt` for every case/repetition pair;
- enforces `EvaluationCase.timeout_seconds` around the executor call;
- contains case execution, timeout and teardown failures as explicit error results rather than silently losing the suite history;
- persists results as they are produced;
- marks the run completed only after all attempts have been handled;
- optionally compares a completed run to an accepted baseline under an explicit `RegressionPolicy`.

`NoopEvaluationIsolation` exists only for already-isolated executors and tests. It is not a substitute for the remaining workspace-backed production isolation implementation.

## Canonical reference executor

`KernelEvaluationCaseExecutor` is the initial reference executor. It deliberately uses the same platform-owned `PlatformKernel` lifecycle as ordinary work instead of introducing an evaluation-only execution lifecycle:

1. create the canonical Task;
2. move the Task to ready;
3. start the Task, which creates and starts the canonical Run;
4. refresh the Run through the lifecycle backend until it reaches a terminal canonical status;
5. read the final Task, Run and canonical Event history;
6. project backend-neutral evaluation evidence.

The resulting `EvaluationObservation` includes the canonical Task/Run IDs and statuses, Run output, Run attempt and dispatch-attempt counts, Artifact and Result references and canonical event types. This makes behavior such as retries, lifecycle transitions and artifact/result production assertable without exposing backend-private execution objects.

## Observation contract

Evaluators consume `EvaluationObservation`, not provider-private runtime objects. It can carry structured result/behavior data, metrics, Task/Run IDs, Artifact references, telemetry references, selected canonical model/provider IDs, capability references and canonical event types.

This permits assertions over non-output behavior such as selected models/tools, approvals, retries, event sequences and artifact provenance once the corresponding runtime layers project those observations into this contract.

## Configuration snapshots

`ConfigurationSnapshot` records the platform version/commit plus unique version references. Reference kinds are intentionally extensible and can identify:

- Agent and AgentTeam revisions;
- orchestrator adapter/version;
- executor adapter/version;
- canonical model configuration and provider versions;
- tool/capability versions;
- prompt/configuration revisions;
- policy revisions;
- dataset/suite/case versions where stored by the runner;
- node/environment metadata.

Two runs are only meaningful to compare when the caller can inspect these snapshots. The framework does not hide relevant configuration inside evaluator code.

## Regression policy

Regression thresholds are explicit versioned data represented by `RegressionPolicy`. The initial engine supports:

- deterministic pass -> fail;
- score drop beyond a configured threshold;
- tagged-case failure, including `critical` or `security` policies;
- absolute metric threshold violation.

The same rules also report corresponding recoveries/improvements where a baseline permits that conclusion.

Baseline matching is performed per `(case_id, evaluator_id)`. Results produced by different evaluators for the same case therefore cannot overwrite or masquerade as one another. Multiple unaggregated results for the same case/evaluator pair are rejected explicitly. This matters for repeated/stochastic evaluations: repetition metadata is already canonical, but an aggregation policy must define how samples become one comparable baseline value before automatic regression comparison is allowed.

Accordingly, the current `EvaluationRunner` allows automatic baseline comparison only with `repetitions=1`. Repeated runs are executable and reproducible now, but stochastic aggregation/comparison remains explicit follow-up work rather than an implicit average hidden in the engine.

`config/evaluation-regression.example.json` demonstrates a no-paid-service PR policy. A later configuration loader will deserialize versioned policy files rather than embedding thresholds in evaluator implementations.

## Persistence

`EvaluationRepository` is the replaceable storage boundary. The initial `InMemoryEvaluationRepository` stores runs, case/evaluator results and comparison reports and is suitable for unit tests and local/reference runs. Production persistence must retain run summaries, case results, configuration metadata, comparison/regression findings and artifact/log references without changing canonical models.

## Current issue #19 implementation status

The implementation is being landed progressively before Control Plane and CLI surfaces, following the repository merge order for canonical contracts and reference behavior first.

Implemented:

- canonical EvaluationCase/Suite/Run/Attempt/Result models;
- run repetitions and seed metadata;
- configuration/version snapshots;
- replaceable evaluator contract;
- deterministic assertion evaluator;
- metric threshold evaluator;
- rubric/model-evaluator boundary metadata;
- evaluator failure containment;
- replaceable evaluation-case executor contract;
- explicit per-attempt isolation lifecycle contract;
- suite runner with repetitions, deterministic seed derivation, timeouts and contained execution errors;
- reference executor through the real canonical PlatformKernel Task/Run lifecycle;
- Task/Run/output/Artifact/Result/Event projection into `EvaluationObservation`;
- replaceable result/comparison persistence contract plus in-memory reference store;
- baseline comparison/regression engine matched by case/evaluator identity;
- explicit rejection of unaggregated repeated results in baseline comparison;
- versioned regression policy model;
- no-paid-service policy example;
- tests for pass/fail, baseline comparison, thresholds, critical/security tags, snapshot integrity, model/provider version differences and evaluator failure handling;
- tests for repetition/seed propagation, isolation ordering, execution-error containment, comparison persistence and real-kernel reference execution.

Remaining work for full issue completion:

- workspace-backed `EvaluationIsolation` implementation with fixture preparation, cleanup and contamination tests;
- durable evaluation persistence implementation and historical/trend queries;
- explicit stochastic aggregation/comparison policies for repeated runs;
- rubric scorer and optional model-judge adapter implementation;
- richer telemetry, accounting and log references in observations and stored results;
- Control Plane Evaluation resources/API;
- CLI commands (`platform eval suite list`, `platform eval run`, `platform eval result show`, `platform eval compare` or equivalent canonical surface);
- deterministic PR-gating workflow driven by canonical evaluation suites and versioned policies;
- Search integration after the canonical Evaluation API is available.

## CI principle

Baseline PR evaluation must stay stable, deterministic and free of paid evaluation services. Heavier stochastic/model-judge suites belong in separately selected integration or release validation.
