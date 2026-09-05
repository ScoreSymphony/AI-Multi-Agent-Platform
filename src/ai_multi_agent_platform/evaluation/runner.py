"""Canonical suite runner for repeatable evaluation execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

from .aggregation import (
    AggregatedEvaluationResult,
    AggregationPolicy,
    ComparableEvaluationResult,
    ResultAggregator,
)
from .context import EvaluationExecutionContext
from .contracts import (
    EvaluationCaseExecutor,
    EvaluationIsolation,
    EvaluationRepository,
    EvaluatorLike,
)
from .evaluators import evaluate_safely
from .hardening import (
    ResourceLimitEvaluator,
    merge_snapshot_references,
    validate_snapshot_reference_kinds,
)
from .models import (
    ComparisonReport,
    ConfigurationSnapshot,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationOutcome,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationSuite,
    RegressionPolicy,
    VersionReference,
    utc_now,
)
from .regression import RegressionEngine


@dataclass(frozen=True, slots=True)
class EvaluationRunSummary:
    """Completed run plus raw/aggregated results and optional baseline comparison."""

    run: EvaluationRun
    results: tuple[EvaluationResult, ...]
    comparison: ComparisonReport | None = None
    aggregates: tuple[AggregatedEvaluationResult, ...] = ()


class NoopEvaluationIsolation:
    """Explicit no-op isolation for unit tests and already-isolated executors."""

    async def reset_case(self, *, case: EvaluationCase, attempt: EvaluationAttempt) -> None:
        del case, attempt

    async def setup_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
    ) -> EvaluationExecutionContext:
        del case
        return EvaluationExecutionContext(attempt_id=attempt.attempt_id)

    async def teardown_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
        succeeded: bool,
    ) -> None:
        del case, attempt, execution_context, succeeded


class EvaluationRunner:
    """Execute versioned suites while keeping execution, isolation and evaluators replaceable."""

    def __init__(
        self,
        *,
        repository: EvaluationRepository,
        executor: EvaluationCaseExecutor,
        evaluators: tuple[EvaluatorLike, ...],
        isolation: EvaluationIsolation | None = None,
        regression_engine: RegressionEngine | None = None,
        result_aggregator: ResultAggregator | None = None,
        configuration_references: tuple[VersionReference, ...] = (),
        required_snapshot_kinds: tuple[str, ...] = (),
        resource_limit_evaluator: ResourceLimitEvaluator | None = None,
    ) -> None:
        if not evaluators:
            raise ValueError("evaluation runner requires at least one evaluator")
        evaluator_ids = [evaluator.descriptor.evaluator_id for evaluator in evaluators]
        if len(evaluator_ids) != len(set(evaluator_ids)):
            raise ValueError("evaluation runner evaluator IDs must be unique")
        reference_identities = [(item.kind, item.ref_id) for item in configuration_references]
        if len(reference_identities) != len(set(reference_identities)):
            raise ValueError("evaluation runner configuration references must be unique")
        normalized_required = tuple(kind.strip() for kind in required_snapshot_kinds)
        if any(not kind for kind in normalized_required):
            raise ValueError("required snapshot reference kinds must not be blank")
        if len(normalized_required) != len(set(normalized_required)):
            raise ValueError("required snapshot reference kinds must be unique")
        self._repository = repository
        self._executor = executor
        self._evaluators = evaluators
        self._isolation = isolation or NoopEvaluationIsolation()
        self._regression_engine = regression_engine or RegressionEngine()
        self._result_aggregator = result_aggregator or ResultAggregator()
        self._configuration_references = configuration_references
        self._required_snapshot_kinds = normalized_required
        self._resource_limit_evaluator = resource_limit_evaluator or ResourceLimitEvaluator()

    async def run_suite(
        self,
        *,
        suite: EvaluationSuite,
        snapshot: ConfigurationSnapshot,
        repetitions: int = 1,
        seed: int | None = None,
        baseline_run_id: str | None = None,
        regression_policy: RegressionPolicy | None = None,
        aggregation_policy: AggregationPolicy | None = None,
    ) -> EvaluationRunSummary:
        """Run every case/repetition and persist canonical results as they are produced."""

        if repetitions <= 0:
            raise ValueError("repetitions must be greater than zero")
        if (baseline_run_id is None) != (regression_policy is None):
            raise ValueError(
                "baseline_run_id and regression_policy must either both be set or both be omitted"
            )
        if baseline_run_id is not None and repetitions != 1 and aggregation_policy is None:
            raise ValueError(
                "automatic baseline comparison with repeated samples requires aggregation_policy; "
                "without aggregation repetitions=1 is required"
            )

        snapshot = self._complete_snapshot(
            suite=suite,
            snapshot=snapshot,
            regression_policy=regression_policy,
            aggregation_policy=aggregation_policy,
        )
        validate_snapshot_reference_kinds(snapshot, self._required_snapshot_kinds)
        baseline = self._validate_baseline(
            suite=suite,
            baseline_run_id=baseline_run_id,
            regression_policy=regression_policy,
            repetitions=repetitions,
            aggregation_policy=aggregation_policy,
        )

        run = EvaluationRun(
            suite_id=suite.suite_id,
            suite_version=suite.version,
            snapshot=snapshot,
            status=EvaluationRunStatus.RUNNING,
            baseline_run_id=baseline_run_id,
            repetitions=repetitions,
            seed=seed,
        )
        self._repository.save_run(run)

        try:
            for repetition_index in range(repetitions):
                repetition_seed = None if seed is None else seed + repetition_index
                for case in suite.cases:
                    attempt = EvaluationAttempt(
                        evaluation_run_id=run.run_id,
                        case_id=case.case_id,
                        case_version=case.version,
                        repetition_index=repetition_index,
                        seed=repetition_seed,
                    )
                    await self._run_attempt(run, case, attempt)
        except Exception:
            failed = replace(run, status=EvaluationRunStatus.FAILED, completed_at=utc_now())
            self._repository.save_run(failed)
            raise

        completed = replace(run, status=EvaluationRunStatus.COMPLETED, completed_at=utc_now())
        self._repository.save_run(completed)
        results = self._repository.list_results(completed.run_id)
        aggregates: tuple[AggregatedEvaluationResult, ...] = ()
        if aggregation_policy is not None:
            aggregates = self._aggregate_and_persist(
                run=completed,
                results=results,
                policy=aggregation_policy,
            )

        comparison: ComparisonReport | None = None
        if baseline is not None and regression_policy is not None:
            baseline_comparable: tuple[ComparableEvaluationResult, ...]
            current_comparable: tuple[ComparableEvaluationResult, ...]
            if aggregation_policy is None:
                baseline_comparable = self._repository.list_results(baseline.run_id)
                current_comparable = results
            else:
                baseline_comparable = self._aggregate_and_persist(
                    run=baseline,
                    results=self._repository.list_results(baseline.run_id),
                    policy=aggregation_policy,
                )
                current_comparable = aggregates
            comparison = self._regression_engine.compare(
                baseline_run_id=baseline.run_id,
                current_run_id=completed.run_id,
                baseline_results=baseline_comparable,
                current_results=current_comparable,
                policy=regression_policy,
            )
            self._repository.save_comparison(comparison)

        return EvaluationRunSummary(
            run=completed,
            results=results,
            comparison=comparison,
            aggregates=aggregates,
        )

    def _complete_snapshot(
        self,
        *,
        suite: EvaluationSuite,
        snapshot: ConfigurationSnapshot,
        regression_policy: RegressionPolicy | None,
        aggregation_policy: AggregationPolicy | None,
    ) -> ConfigurationSnapshot:
        runtime_references: list[VersionReference] = list(self._configuration_references)
        runtime_references.append(
            VersionReference(
                kind="evaluation_suite",
                ref_id=suite.suite_id,
                version=suite.version,
            )
        )
        for evaluator in self._evaluators:
            runtime_references.append(
                VersionReference(
                    kind="evaluator",
                    ref_id=evaluator.descriptor.evaluator_id,
                    version=evaluator.descriptor.version,
                )
            )
        if any(case.resource_limits for case in suite.cases):
            runtime_references.append(
                VersionReference(
                    kind="evaluator",
                    ref_id=self._resource_limit_evaluator.descriptor.evaluator_id,
                    version=self._resource_limit_evaluator.descriptor.version,
                )
            )
        if regression_policy is not None:
            runtime_references.append(
                VersionReference(
                    kind="regression_policy",
                    ref_id=regression_policy.policy_id,
                    version=regression_policy.version,
                )
            )
        if aggregation_policy is not None:
            runtime_references.append(
                VersionReference(
                    kind="aggregation_policy",
                    ref_id=aggregation_policy.policy_id,
                    version=aggregation_policy.version,
                )
            )
        return merge_snapshot_references(snapshot, tuple(runtime_references))

    def _validate_baseline(
        self,
        *,
        suite: EvaluationSuite,
        baseline_run_id: str | None,
        regression_policy: RegressionPolicy | None,
        repetitions: int,
        aggregation_policy: AggregationPolicy | None,
    ) -> EvaluationRun | None:
        if baseline_run_id is None or regression_policy is None:
            return None
        baseline = self._repository.get_run(baseline_run_id)
        if baseline is None:
            raise ValueError(f"evaluation baseline run not found: {baseline_run_id}")
        if baseline.status is not EvaluationRunStatus.COMPLETED:
            raise ValueError("evaluation baseline run must be completed")
        if baseline.suite_id != suite.suite_id or baseline.suite_version != suite.version:
            raise ValueError("baseline suite identity/version does not match current suite")
        if baseline.repetitions != 1 and aggregation_policy is None:
            raise ValueError(
                "repeated baseline comparison requires aggregation_policy; "
                "without aggregation the baseline must use repetitions=1"
            )
        if (
            aggregation_policy is not None
            and aggregation_policy.require_equal_sample_count
            and baseline.repetitions != repetitions
        ):
            raise ValueError(
                "aggregation policy requires baseline and current runs to use the same "
                "repetition count"
            )
        return baseline

    def _aggregate_and_persist(
        self,
        *,
        run: EvaluationRun,
        results: tuple[EvaluationResult, ...],
        policy: AggregationPolicy,
    ) -> tuple[AggregatedEvaluationResult, ...]:
        aggregates = self._result_aggregator.aggregate(
            results=results,
            policy=policy,
            expected_repetitions=run.repetitions,
        )
        for aggregate in aggregates:
            self._repository.save_aggregate(aggregate)
        return aggregates

    async def _run_attempt(
        self,
        run: EvaluationRun,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
    ) -> None:
        observation = None
        execution_context: EvaluationExecutionContext | None = None
        setup_complete = False
        execution_error: Exception | None = None
        error_category = "case_execution_failure"

        try:
            await self._isolation.reset_case(case=case, attempt=attempt)
            execution_context = await self._isolation.setup_case(case=case, attempt=attempt)
            setup_complete = True
            if execution_context.attempt_id != attempt.attempt_id:
                raise ValueError("evaluation isolation returned context for another attempt")
            if case.timeout_seconds is None:
                observation = await self._executor.execute_case(
                    case=case,
                    attempt=attempt,
                    execution_context=execution_context,
                )
            else:
                async with asyncio.timeout(case.timeout_seconds):
                    observation = await self._executor.execute_case(
                        case=case,
                        attempt=attempt,
                        execution_context=execution_context,
                    )
        except TimeoutError as exc:
            execution_error = exc
            error_category = "case_execution_timeout"
        except Exception as exc:
            execution_error = exc
        finally:
            if setup_complete and execution_context is not None:
                try:
                    await self._isolation.teardown_case(
                        case=case,
                        attempt=attempt,
                        execution_context=execution_context,
                        succeeded=execution_error is None,
                    )
                except Exception as exc:
                    if execution_error is None:
                        execution_error = exc
                        error_category = "case_teardown_failure"

        if execution_error is not None:
            self._save_execution_errors(run, case, attempt, execution_error, error_category)
            return

        assert observation is not None
        if case.resource_limits:
            resource_result = await evaluate_safely(
                self._resource_limit_evaluator,
                evaluation_run_id=run.run_id,
                case=case,
                observation=observation,
            )
            self._repository.save_result(
                replace(
                    resource_result,
                    attempt_id=attempt.attempt_id,
                    repetition_index=attempt.repetition_index,
                    seed=attempt.seed,
                )
            )

        for evaluator in self._evaluators:
            result = await evaluate_safely(
                evaluator,
                evaluation_run_id=run.run_id,
                case=case,
                observation=observation,
            )
            self._repository.save_result(
                replace(
                    result,
                    attempt_id=attempt.attempt_id,
                    repetition_index=attempt.repetition_index,
                    seed=attempt.seed,
                )
            )

    def _save_execution_errors(
        self,
        run: EvaluationRun,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        error: Exception,
        error_category: str,
    ) -> None:
        evaluators: tuple[EvaluatorLike, ...]
        if case.resource_limits:
            evaluators = (self._resource_limit_evaluator, *self._evaluators)
        else:
            evaluators = self._evaluators
        for evaluator in evaluators:
            self._repository.save_result(
                EvaluationResult(
                    evaluation_run_id=run.run_id,
                    case_id=case.case_id,
                    case_version=case.version,
                    evaluator=evaluator.descriptor,
                    outcome=EvaluationOutcome.ERROR,
                    case_tags=case.tags,
                    attempt_id=attempt.attempt_id,
                    repetition_index=attempt.repetition_index,
                    seed=attempt.seed,
                    error_category=error_category,
                    error_message=str(error),
                )
            )
