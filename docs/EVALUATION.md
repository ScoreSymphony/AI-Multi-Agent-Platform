# Evaluation and Regression Framework

Issue #19 owns the platform-level evaluation and regression layer. The framework is provider-neutral, orchestrator-neutral and evaluator-neutral. Unit, integration and contract tests remain separate concerns; evaluations measure repeatable platform, agent, model, tool and workflow behavior across versioned scenarios.

## Canonical ownership

The platform owns these concepts:

- `EvaluationCase`: one versioned scenario with explicit inputs, fixtures, deterministic assertions, metric thresholds, optional rubric criteria, tags and resource limits.
- `EvaluationSuite`: a versioned collection of cases.
- `ConfigurationSnapshot`: immutable version references for the platform build and the participating agent, model, provider, orchestrator, executor, prompt, capability, policy and environment identities.
- `EvaluationRun`: one execution of a suite/configuration snapshot with repetitions, optional seed and optional accepted baseline run.
- `EvaluationAttempt`: one specific case repetition, with stable attempt identity, repetition index and resolved seed.
- `EvaluationExecutionContext`: attempt-scoped execution state produced by isolation and consumed explicitly by the case executor. It may carry canonical Workspace/Snapshot identity plus an opaque materialization token, but never a host filesystem path as canonical identity.
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

Missing metric observations are represented by the absence of an observed `MetricResult`, not by a synthetic `NaN` value. The metric evaluator still fails the case, while persistence remains strict JSON and the regression engine can distinguish a missing required metric from an observed threshold violation.

## Runner and execution boundary

`EvaluationRunner` owns suite-level evaluation execution without owning platform Task/Run lifecycle semantics. It consumes two replaceable boundaries:

- `EvaluationCaseExecutor`, which executes one case attempt and returns an `EvaluationObservation`;
- `EvaluationIsolation`, which provides explicit `reset_case -> setup_case -> execute -> teardown_case` isolation around every attempt and returns the attempt-scoped `EvaluationExecutionContext`.

The runner:

- persists a running `EvaluationRun` before executing cases;
- expands every case into the requested number of repetitions;
- derives repetition seeds deterministically as `base_seed + repetition_index` when a base seed is supplied;
- creates an `EvaluationAttempt` for every case/repetition pair;
- obtains an explicit execution context from isolation and passes the same context to the executor and teardown path;
- enforces `EvaluationCase.timeout_seconds` around the executor call;
- contains case execution, timeout and teardown failures as explicit error results rather than silently losing the suite history;
- persists results as they are produced;
- marks the run completed only after all attempts have been handled;
- optionally compares a completed run to an accepted baseline under an explicit `RegressionPolicy`.

`NoopEvaluationIsolation` exists only for already-isolated executors and tests. It returns an empty attempt-scoped execution context rather than creating mutable state.

## Workspace-backed isolation

`WorkspaceEvaluationIsolation` is the reference production-style isolation implementation. It reuses the canonical Workspace subsystem rather than maintaining evaluation-private directories or snapshots.

For every attempt it:

1. resolves declared `EvaluationCase.fixtures` through the replaceable `EvaluationFixtureResolver` boundary;
2. creates a fresh `WorkspaceType.ISOLATED_RUN` workspace with `WorkspaceRetention.EPHEMERAL`;
3. creates/uses the workspace's immutable canonical base `WorkspaceSnapshot`;
4. materializes that exact snapshot into a fresh bounded execution workspace;
5. returns canonical `workspace_id`, `workspace_snapshot_id`, snapshot checksum and the opaque materialization/execution token in `EvaluationExecutionContext`;
6. releases the materialization after the attempt;
7. deliberately does **not** commit attempt mutations back into the canonical workspace snapshot.

This gives each repetition a clean starting state. A later repetition rehydrates the fixture from canonical file evidence instead of inheriting files modified by the previous attempt. Tests explicitly mutate a fixture in one materialization and verify that the next attempt sees the original fixture bytes.

`EvaluationFixture` stores fixture IDs plus canonical `WorkspaceFile`/`WorkspaceSourceRef` evidence. `StaticEvaluationFixtureResolver` is the deterministic checked-in/reference resolver. Cases that declare fixture IDs fail explicitly if no resolver is configured; fixture requirements are never silently ignored.

The opaque `execution_workspace` token is implementation-local routing information. It is not a canonical Workspace ID and must not be persisted or compared as if it were one.

## Canonical reference executor

`KernelEvaluationCaseExecutor` is the initial reference executor. It deliberately uses the same platform-owned `PlatformKernel` lifecycle as ordinary work instead of introducing an evaluation-only execution lifecycle:

1. create the canonical Task;
2. move the Task to ready;
3. plan when needed;
4. create the canonical Run in queued state;
5. when a workspace-backed execution context is present, bind that Run to the exact canonical Workspace/Snapshot through `RunWorkspaceBindingRepository`;
6. start the Run only after the binding exists;
7. refresh the Run through the lifecycle backend until it reaches a terminal canonical status;
8. read the final Task, Run and canonical Event history;
9. project backend-neutral evaluation evidence.

The resulting `EvaluationObservation` includes the canonical Task/Run IDs and statuses, Run output, Run attempt and dispatch-attempt counts, Artifact and Result references, canonical event types and canonical Workspace/Snapshot identity when one was bound. This makes behavior such as retries, lifecycle transitions, workspace provenance and artifact/result production assertable without exposing backend-private execution objects.

The reference executor validates workspace owner/project scope against its configured Task owner/project. Workspace-backed kernel execution requires the canonical `RunWorkspaceBindingRepository`; it does not hide workspace identity in evaluator data or mutate a lifecycle backend's private workspace setting.

## Observation contract

Evaluators consume `EvaluationObservation`, not provider-private runtime objects. It can carry structured result/behavior data, metrics, Task/Run IDs, Artifact references, telemetry references, selected canonical model/provider IDs, capability references and canonical event types.

This permits assertions over non-output behavior such as selected models/tools, approvals, retries, event sequences, workspace provenance and artifact provenance once the corresponding runtime layers project those observations into this contract.

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

## Regression policy and checked-in configuration

Regression thresholds are explicit versioned data represented by `RegressionPolicy`. The initial engine supports:

- deterministic pass -> fail;
- score drop beyond a configured threshold;
- tagged-case failure, including `critical` or `security` policies;
- absolute metric threshold violation.

The same rules also report corresponding recoveries/improvements where a baseline permits that conclusion.

Baseline matching is performed per `(case_id, evaluator_id)`. Results produced by different evaluators for the same case therefore cannot overwrite or masquerade as one another. Multiple unaggregated results for the same case/evaluator pair are rejected explicitly. This matters for repeated/stochastic evaluations: repetition metadata is already canonical, but an aggregation policy must define how samples become one comparable baseline value before automatic regression comparison is allowed.

Accordingly, the current `EvaluationRunner` allows automatic baseline comparison only with `repetitions=1`. Repeated runs are executable and reproducible now, but stochastic aggregation/comparison remains explicit follow-up work rather than an implicit average hidden in the engine.

The framework now includes strict JSON loaders for checked-in `EvaluationSuite`, `RegressionPolicy` and accepted Evaluation baseline assets. Duplicate JSON keys, unknown fields and invalid field types are rejected rather than silently accepted. Accepted baselines are deserialized through the existing strict canonical `EvaluationRun`/`EvaluationResult` codec and validated against suite/case identity and version.

The deterministic PR profile is versioned in:

- `config/evaluation-suite.pr-deterministic.json`;
- `config/evaluation-regression.pr-deterministic.json`;
- `config/evaluation-baseline.pr-deterministic.json`.

The accepted baseline must contain exactly one result for every required `(case_id, evaluator_id)` pair and only passing results. The reference PR profile uses `repetitions=1` and deterministic seed `19`.

The concrete `dispatch_attempts <= 1` threshold belongs to the versioned `MetricRule` in the EvaluationCase and is evaluated by `MetricThresholdEvaluator`. The PR `RegressionPolicy` then classifies deterministic pass-to-fail, score-drop and tagged critical/security regressions. This avoids duplicating thresholds in workflow YAML or evaluator implementation code and avoids applying an unrelated global metric threshold to evaluator results that do not emit that metric.

## Deterministic no-paid PR CI gate

`run_reference_ci_gate(...)` turns the canonical Evaluation framework into the required lightweight PR gate. It composes existing platform-owned reference components:

```text
ReferenceOrchestrator
    -> PlatformKernel
    -> KernelEvaluationCaseExecutor
    -> ExecutorLifecycleBackend
    -> ReferenceExecutor
    -> EvaluationObservation
    -> DeterministicAssertionEvaluator / MetricThresholdEvaluator
    -> EvaluationRunner
    -> RegressionEngine
```

The gate therefore evaluates the same canonical Task/Run lifecycle as ordinary platform work. It does not introduce a fake evaluation-only lifecycle and requires no LLM judge, model endpoint, paid API, Hermes runtime or Forge runtime. The generated `ConfigurationSnapshot` records the current platform version/commit plus the orchestrator, executor, both evaluator identities, exact regression-policy version and exact suite version.

The checked-in reference case verifies canonical Task and Run success, one dispatch attempt, output presence, required lifecycle events and the versioned dispatch-attempt metric threshold. Tests also run a deliberately regressed suite and verify that the normal `RegressionEngine` reports deterministic pass-to-fail, score-drop and critical-case regressions against the accepted baseline.

The executable CI entry point is `scripts/ci/issue19_evaluation_gate.py`. It emits a compact JSON summary containing suite/policy/baseline/current-run identity and regression counts. Its exit contract is:

- `0` when the deterministic run and accepted-baseline comparison pass;
- `1` for a failed EvaluationResult or classified regression;
- `2` for configuration or gate-execution setup errors.

The repository's main CI runs `Deterministic evaluation gate` after the full Pytest suite and before package build. Because the existing Hermes and Forge compatibility jobs depend on the main `test` job, a failed Evaluation gate blocks those downstream checks rather than becoming an optional detached signal.

This narrow reference profile fails explicitly when a case declares fixtures, rubric criteria or resource limits whose semantics the profile does not enforce. Such suites must use the canonical workspace isolation, rubric evaluator or resource-accounting paths rather than having requirements silently ignored.

## Persistence and historical trends

`EvaluationRepository` is the minimal replaceable storage boundary consumed by the runner. `EvaluationHistoryRepository` extends it with indexed historical queries for runs and case/evaluator results.

Two reference implementations exist:

- `InMemoryEvaluationRepository` for deterministic unit/reference execution;
- `SqliteEvaluationRepository` as the restart-safe stdlib-only durable baseline.

The SQLite repository stores canonical Run, Result and Comparison payloads as strict JSON while retaining indexed columns for suite/version, case/evaluator, outcome, repetition and timestamps. `json.dumps(..., allow_nan=False)` is enforced; non-JSON numeric states are rejected as contract violations instead of entering historical data. A storage-schema metadata record makes incompatible future schema revisions detectable rather than silently mis-decoding them.

Durable behavior includes:

- upserted `EvaluationRun` state so a running record can become completed/failed without changing its public identity;
- Result persistence attached to an existing run;
- ComparisonReport persistence only when current and baseline runs exist;
- `list_runs(...)` filtered by suite identity/version, with the historical default limit retained and an internal `limit=None` mode for complete Control Plane pagination;
- `list_case_results(...)` filtered by case and optional evaluator identity;
- restart-safe baseline comparison: a new runner can reopen SQLite and compare against a baseline produced before the process restart.

`EvaluationHistoryService.case_trend(...)` converts historical case/evaluator results into `EvaluationTrendPoint` records enriched with the corresponding suite and immutable ConfigurationSnapshot identity (`snapshot_id`, platform version and commit). This keeps trend interpretation tied to the configuration that produced each measurement instead of plotting scores without provenance.

Workspace persistence remains owned by the Workspace subsystem. Evaluation persistence references canonical Workspace/Snapshot/Artifact evidence rather than duplicating workspace bytes or materialization paths.

## Control Plane API

`EvaluationService` is the application boundary between northbound surfaces and the canonical runner/history layer. Configured suites and regression policies are addressed by exact versioned references of the form `<id>@<version>`; the service delegates execution to `EvaluationRunner`, comparison to `RegressionEngine` and persistence to `EvaluationHistoryRepository` rather than creating parallel lifecycle state.

The initial Control Plane surface registers:

- `evaluation-suites` for configured versioned suite discovery and inspection;
- `evaluation-runs` for durable run history and single-run detail including evaluator results and a stored comparison report;
- `evaluation.run` to execute an exact configured suite with an explicit `ConfigurationSnapshot`, repetitions/seed and optional baseline/policy references;
- `evaluation.compare` to compare two completed single-repetition runs under an exact versioned `RegressionPolicy` and persist the resulting `ComparisonReport`.

Run-list pagination remains owned by the generic Control Plane `PageQuery`/cursor machinery. The Evaluation resource service therefore supplies the complete internal run history to the generic paginator rather than pre-truncating it to the repository's normal 100-run history default. Tests explicitly create 105 runs and verify that the second API page remains reachable. The SQLite implementation separately exercises the unbounded internal read path.

The Control Plane serializes canonical evaluation records through the existing strict evaluation codec. It does not invent a second EvaluationRun/Result wire lifecycle or expose backend-private execution objects.

## API-first CLI

The canonical CLI exposes the Evaluation API without constructing an `EvaluationRunner` or reading Evaluation persistence directly:

- `platform eval suite list`;
- `platform eval suite show <suite-ref>`;
- `platform eval run <suite-ref> --snapshot-json ...`;
- `platform eval result show <run-id>`;
- `platform eval compare <current-run-id> --baseline-run-id ... --regression-policy-ref ...`.

Suite reads use `/api/v1/evaluation-suites`; run detail uses `/api/v1/evaluation-runs`; mutations use `/api/v1/commands/evaluation.run` and `/api/v1/commands/evaluation.compare`. The CLI sends explicit immutable snapshot data and versioned suite/policy references and relies on the Control Plane for canonical domain validation and execution semantics.

CLI contract tests verify URL encoding for versioned suite refs, pagination/filter forwarding, exact mutation payloads, explicit idempotency keys, durable result reads and local rejection of invalid snapshot/repetition input before transport.

## Global Search integration

Issue #45 now projects canonical Evaluation discovery into the global Search layer without making Search authoritative for Evaluation state. Search consumes only the registered Control Plane Evaluation resources:

- `evaluation-suites` -> `evaluation-suite` discovery documents;
- `evaluation-runs` -> `evaluation-run` discovery documents.

Only approved top-level discovery metadata is indexed. Nested case content, fixtures, input templates, assertions, rubric criteria, snapshot environment values, evaluator descriptors, Evaluation results, metric values/thresholds and comparison/regression findings are deliberately excluded from global search text. Authorization is applied using the existing Evaluation collection vocabulary before totals or exact-ID results become visible.

Search remains a derived navigation layer and links callers back to the canonical `/api/v1/evaluation-suites/{suite-ref}` and `/api/v1/evaluation-runs/{run-id}` resources. It does not read `EvaluationHistoryRepository`, `EvaluationRunner` internals or provider state directly, and it requires no external Search backend or paid service.

## Current issue #19 implementation status

The implementation is being landed progressively from canonical contracts/reference behavior outward through persistence and northbound surfaces.

Implemented:

- canonical EvaluationCase/Suite/Run/Attempt/Result models;
- run repetitions and seed metadata;
- configuration/version snapshots;
- replaceable evaluator contract;
- deterministic assertion evaluator;
- metric threshold evaluator with no synthetic NaN state for missing observations;
- rubric/model-evaluator boundary metadata;
- evaluator failure containment;
- replaceable evaluation-case executor contract;
- explicit per-attempt isolation lifecycle and execution-context contract;
- suite runner with repetitions, deterministic seed derivation, timeouts and contained execution errors;
- reference executor through the real canonical PlatformKernel Task/Run lifecycle;
- canonical Run -> WorkspaceSnapshot binding before run start;
- workspace-backed isolated-run creation/materialization/release;
- replaceable fixture resolution and deterministic static fixture resolver;
- explicit no-commit isolation semantics for attempt mutations;
- Task/Run/output/Artifact/Result/Event/Workspace projection into `EvaluationObservation`;
- replaceable persistence contract plus in-memory reference store;
- durable stdlib SQLite run/result/comparison persistence with storage schema marker;
- historical run and case/evaluator queries;
- ConfigurationSnapshot-enriched case trend projection;
- restart-safe comparison against durable baselines;
- baseline comparison/regression engine matched by case/evaluator identity;
- explicit rejection of unaggregated repeated results in baseline comparison;
- versioned regression policy model;
- strict checked-in EvaluationSuite/RegressionPolicy/baseline configuration loading;
- Control Plane `evaluation-suites` and `evaluation-runs` resources;
- Control Plane `evaluation.run` and `evaluation.compare` commands backed by `EvaluationService`;
- complete generic cursor pagination over evaluation-run history without the repository default-limit truncation;
- API-first `platform eval` suite/run/result/compare CLI surface;
- checked-in deterministic PR suite, regression policy and accepted canonical baseline;
- real no-paid deterministic PR Evaluation gate through the canonical Task/Run reference path;
- CI integration that blocks the main test job on Evaluation regression;
- global Search discovery for canonical Evaluation suites/runs with explicit privacy and authorization boundaries;
- tests for pass/fail, baseline comparison, thresholds, critical/security tags, snapshot integrity, model/provider version differences and evaluator failure handling;
- tests for repetition/seed propagation, isolation ordering, execution-error containment, comparison persistence and real-kernel reference execution;
- tests proving cross-attempt workspace contamination is prevented and Run workspace binding exists before lifecycle start;
- tests for strict JSON persistence, SQLite restart/history/trend queries and baseline comparison after repository restart;
- tests for Control Plane manifest/OpenAPI exposure, HTTP run/compare flow, persisted comparison reads and run-history pagination beyond 100 records;
- tests for the API-first Evaluation CLI request/payload/validation contract;
- tests for strict Evaluation configuration loading, real reference CI-gate execution and deliberate baseline regression detection.

Remaining work for full issue completion:

- explicit stochastic aggregation/comparison policies for repeated runs;
- rubric scorer and optional model-judge adapter implementation;
- richer telemetry, accounting and log references in observations and stored results.

## CI principle

Baseline PR evaluation stays stable, deterministic and free of paid evaluation services. The checked-in PR profile runs only platform-owned reference components and deterministic evaluators. Heavier stochastic suites, resource-intensive scenarios and optional model-judge suites belong in separately selected integration or release validation and must not become a mandatory paid CI dependency.
