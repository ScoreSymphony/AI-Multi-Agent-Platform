"""Reference in-memory persistence for evaluation runs, results, aggregates and comparisons."""

from __future__ import annotations

from threading import RLock

from .aggregation import AggregatedEvaluationResult
from .models import ComparisonReport, EvaluationResult, EvaluationRun


def _require_limit(limit: int | None) -> None:
    if limit is not None and limit <= 0:
        raise ValueError("evaluation history limit must be greater than zero")


class InMemoryEvaluationRepository:
    """Small replaceable repository suitable for tests and local reference execution."""

    def __init__(self) -> None:
        self._runs: dict[str, EvaluationRun] = {}
        self._results: dict[str, list[EvaluationResult]] = {}
        self._aggregates: dict[str, list[AggregatedEvaluationResult]] = {}
        self._comparisons: dict[str, ComparisonReport] = {}
        self._lock = RLock()

    def save_run(self, run: EvaluationRun) -> None:
        with self._lock:
            self._runs[run.run_id] = run

    def get_run(self, run_id: str) -> EvaluationRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def list_runs(
        self,
        *,
        suite_id: str | None = None,
        suite_version: str | None = None,
        limit: int | None = 100,
    ) -> tuple[EvaluationRun, ...]:
        _require_limit(limit)
        with self._lock:
            runs = tuple(self._runs.values())
        filtered = (
            run
            for run in runs
            if (suite_id is None or run.suite_id == suite_id)
            and (suite_version is None or run.suite_version == suite_version)
        )
        ordered = tuple(sorted(filtered, key=lambda run: run.started_at, reverse=True))
        return ordered if limit is None else ordered[:limit]

    def save_result(self, result: EvaluationResult) -> None:
        with self._lock:
            results = self._results.setdefault(result.evaluation_run_id, [])
            for index, existing in enumerate(results):
                if existing.result_id == result.result_id:
                    results[index] = result
                    return
            results.append(result)

    def list_results(self, evaluation_run_id: str) -> tuple[EvaluationResult, ...]:
        with self._lock:
            return tuple(self._results.get(evaluation_run_id, ()))

    def save_aggregate(self, aggregate: AggregatedEvaluationResult) -> None:
        with self._lock:
            aggregates = self._aggregates.setdefault(aggregate.evaluation_run_id, [])
            for index, existing in enumerate(aggregates):
                if existing.result_id == aggregate.result_id:
                    aggregates[index] = aggregate
                    return
            aggregates.append(aggregate)

    def list_aggregates(
        self,
        evaluation_run_id: str,
        *,
        aggregation_policy_id: str | None = None,
        aggregation_policy_version: str | None = None,
    ) -> tuple[AggregatedEvaluationResult, ...]:
        with self._lock:
            aggregates = tuple(self._aggregates.get(evaluation_run_id, ()))
        return tuple(
            aggregate
            for aggregate in aggregates
            if (
                aggregation_policy_id is None
                or aggregate.aggregation_policy_id == aggregation_policy_id
            )
            and (
                aggregation_policy_version is None
                or aggregate.aggregation_policy_version == aggregation_policy_version
            )
        )

    def list_case_results(
        self,
        *,
        case_id: str,
        evaluator_id: str | None = None,
        limit: int = 100,
    ) -> tuple[EvaluationResult, ...]:
        if not case_id.strip():
            raise ValueError("evaluation history case_id must not be blank")
        _require_limit(limit)
        with self._lock:
            results = tuple(result for items in self._results.values() for result in items)
        filtered = (
            result
            for result in results
            if result.case_id == case_id
            and (evaluator_id is None or result.evaluator.evaluator_id == evaluator_id)
        )
        return tuple(sorted(filtered, key=lambda result: result.created_at, reverse=True))[:limit]

    def save_comparison(self, comparison: ComparisonReport) -> None:
        with self._lock:
            self._comparisons[comparison.current_run_id] = comparison

    def get_comparison(self, current_run_id: str) -> ComparisonReport | None:
        with self._lock:
            return self._comparisons.get(current_run_id)
