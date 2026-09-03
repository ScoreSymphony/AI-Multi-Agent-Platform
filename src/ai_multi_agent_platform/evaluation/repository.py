"""Reference in-memory persistence for evaluation runs, results and comparisons."""

from __future__ import annotations

from threading import RLock

from .models import ComparisonReport, EvaluationResult, EvaluationRun


class InMemoryEvaluationRepository:
    """Small replaceable repository suitable for tests and local reference execution."""

    def __init__(self) -> None:
        self._runs: dict[str, EvaluationRun] = {}
        self._results: dict[str, list[EvaluationResult]] = {}
        self._comparisons: dict[str, ComparisonReport] = {}
        self._lock = RLock()

    def save_run(self, run: EvaluationRun) -> None:
        with self._lock:
            self._runs[run.run_id] = run

    def get_run(self, run_id: str) -> EvaluationRun | None:
        with self._lock:
            return self._runs.get(run_id)

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

    def save_comparison(self, comparison: ComparisonReport) -> None:
        with self._lock:
            self._comparisons[comparison.current_run_id] = comparison

    def get_comparison(self, current_run_id: str) -> ComparisonReport | None:
        with self._lock:
            return self._comparisons.get(current_run_id)
