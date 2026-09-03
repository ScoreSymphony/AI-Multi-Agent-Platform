# Evaluation and Regression Framework

Issue #19 owns the platform-level evaluation and regression layer. The framework is provider-neutral, orchestrator-neutral and evaluator-neutral. Unit, integration and contract tests remain separate concerns; evaluations measure repeatable platform, agent, model, tool and workflow behavior across versioned scenarios.

## Canonical ownership

The platform owns these concepts:

- `EvaluationCase`: one versioned scenario with explicit inputs, fixtures, deterministic assertions, metric thresholds, optional rubric criteria, tags and resource limits.
- `EvaluationSuite`: a versioned collection of cases.
- `ConfigurationSnapshot`: immutable version references for the platform build and the participating agent, model, provider, orchestrator, executor, prompt, capability, policy and environment identities.
- `EvaluationRun`: one execution of a suite/configuration snapshot with an optional accepted baseline run.
- `EvaluationResult`: one evaluator's result for one case, including pass/fail/error, optional score, assertion evidence, metrics and canonical task/run/artifact/telemetry references.
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

## Observation contract

Evaluators consume `EvaluationObservation`, not provider-private runtime objects. It can carry structured result/behavior data, metrics, Task/Run IDs, Artifact references, telemetry references, selected canonical model/provider IDs, capability references and canonical event types.

This permits assertions over non-output behavior such as selected models/tools, approvals, retries, event sequences and artifact provenance once the execution layer projects those observations into this contract.

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

`config/evaluation-regression.example.json` demonstrates a no-paid-service PR policy. A later configuration loader will deserialize versioned policy files rather than embedding thresholds in evaluator implementations.

## Persistence

`EvaluationRepository` is the replaceable storage boundary. The initial `InMemoryEvaluationRepository` is suitable for unit tests and local/reference runs. Production persistence must retain run summaries, case results, configuration metadata, comparison/regression findings and artifact/log references without changing canonical models.

## Current issue #19 implementation status

This foundation intentionally lands before Control Plane and CLI surfaces, following the repository merge order for canonical contracts first.

Implemented in the foundation:

- canonical EvaluationCase/Suite/Run/Result models;
- configuration/version snapshots;
- replaceable evaluator contract;
- deterministic assertion evaluator;
- metric threshold evaluator;
- rubric/model-evaluator boundary metadata;
- evaluator failure containment;
- replaceable result persistence contract plus in-memory reference store;
- baseline comparison/regression engine;
- versioned regression policy model;
- no-paid-service policy example;
- unit tests for pass/fail, baseline comparison, thresholds, critical/security tags, snapshot integrity, model/provider version differences and evaluator failure handling.

Remaining work for full issue completion:

- evaluation runner that creates isolated workspaces, executes cases and captures canonical observations from Task/Run/Artifact/telemetry state;
- durable persistence implementation and trend queries;
- rubric scorer and optional model-judge adapter implementation;
- deterministic local/reference CI suite driven through the real kernel/executor path;
- Control Plane API/resource projection;
- CLI commands;
- explicit isolated-workspace reset/repetition tests;
- artifact/log retention linkage and richer observability/resource-accounting metrics;
- Search integration after the canonical Evaluation API is available.

## CI principle

Baseline PR evaluation must stay stable, deterministic and free of paid evaluation services. Heavier stochastic/model-judge suites belong in separately selected integration or release validation.
