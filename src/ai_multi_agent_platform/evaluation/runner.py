"""Canonical suite runner for repeatable evaluation execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

from .contracts import (
    EvaluationCaseExecutor,
    EvaluationIsolation,
    EvaluationRepository,
    Evaluator,
)
from .evaluators import SafeEvaluator
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
    utc_now,
)
from .regression import RegressionEngine


@dataclass(frozen=True, slots=True)
class EvaluationRunSummary:
    """Completed run plus its persisted results and optional baseline comparison."""

    run: EvaluationRun
    results: tuple[EvaluationResult, ...]
    comparison: ComparisonReport | None = None


class NoopEvaluationIsolation:
    """Explicit no-op isolation for unit tests and already-isolated executors."""

    async def reset_case(self, *, case: EvaluationCase, attempt: EvaluationAttempt) -> None:
        del case, attempt

    async def setup_case(self, *, case: EvaluationCase, attempt: EvaluationAttempt) -> None:
        del case, attempt

    async def teardown_case(self, *, case: EvaluationCase, attempt: EvaluationAttempt) -> None:
        del case, attempt


class EvaluationRunner:
    """Execute versioned suites while keeping execution and isolation replaceable."""

    def __init__(
        self,
        *,
        repository: EvaluationRepository,
        executor: EvaluationCaseExecutor,
        evaluators: tuple[Evaluator, ...],
        isolation: EvaluationIsolation | None = None,
        regression_engine: RegressionEngine | None = None,
    ) -> None:
        if not evaluators:
            raise ValueError("evaluation runner requires at least one evaluator")
        evaluator_ids = [evaluator.descriptor.evaluator_id for evaluator in evaluators]
        if len(evaluator_ids) != len(set(evaluator_ids)):
            raise ValueError("evaluation runner evaluator IDs must be unique")
        self._repository = repository
        self._executor = executor
        self._evaluators = evaluators
        self._isolation = isolation or NoopEvaluationIsolation()
        self._regression_engine = regression_engine or RegressionEngine()

    async def run_suite(
        self,
        *,
        suite: EvaluationSuite,
        snapshot: ConfigurationSnapshot,
        repetitions: int = 1,
        seed: int | None = None,
        baseline_run_id: str | None = None,
        regression_policy: RegressionPolicy | None = None,
    ) -> EvaluationRunSummary:
        """Run every case/repetition and persist canonical results as they are produced."""

        if repetitions <= 0:
            raise ValueError("repetitions must be greater than zero")
        if (baseline_run_id is None) != (regression_policy is None):
            raise ValueError(
                "baseline_run_id and regression_policy must either both be set or both be omitted"
            )
        if baseline_run_id is not None and repetitions != 1:
            raise ValueError(
                "automatic baseline comparison currently requires repetitions=1; "
                "stochastic aggregation is a separate regression policy concern"
            )

        baseline = self._validate_baseline(
            suite=suite,
            baseline_run_id=baseline_run_id,
            regression_policy=regression_policy,
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

        comparison: ComparisonReport | None = None
        if baseline is not None and regression_policy is not None:
            comparison = self._regression_engine.compare(
                baseline_run_id=baseline.run_id,
                current_run_id=completed.run_id,
                baseline_results=self._repository.list_results(baseline.run_id),
                current_results=results,
                policy=regression_policy,
            )
            self._repository.save_comparison(comparison)

        return EvaluationRunSummary(run=completed, results=results, comparison=comparison)

    def _validate_baseline(
        self,
        *,
        suite: EvaluationSuite,
        baseline_run_id: str | None,
        regression_policy: RegressionPolicy | None,
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
        if baseline.repetitions != 1:
            raise ValueError(
                "automatic baseline comparison currently requires a single-repetition baseline"
            )
        return baseline

    async def _run_attempt(
        self,
        run: EvaluationRun,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
    ) -> None:
        observation = None
        execution_error: Exception | None = None
        error_category = "case_execution_failure"

        try:
            await self._isolation.reset_case(case=case, attempt=attempt)
            await self._isolation.setup_case(case=case, attempt=attempt)
            if case.timeout_seconds is None:
                observation = await self._executor.execute_case(case=case, attempt=attempt)
            else:
                async with asyncio.timeout(case.timeout_seconds):
                    observation = await self._executor.execute_case(case=case, attempt=attempt)
        except TimeoutError as exc:
            execution_error = exc
            error_category = "case_execution_timeout"
        except Exception as exc:
            execution_error = exc
        finally:
            try:
                await self._isolation.teardown_case(case=case, attempt=attempt)
            except Exception as exc:
                if execution_error is None:
                    execution_error = exc
                    error_category = "case_teardown_failure"

        if execution_error is not None:
            self._save_execution_errors(run, case, attempt, execution_error, error_category)
            return

        assert observation is not None
        for evaluator in self._evaluators:
            result = SafeEvaluator(evaluator).evaluate(
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
        for evaluator in self._evaluators:
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
