"""Replaceable evaluation, execution-isolation and persistence contracts."""

from __future__ import annotations

from typing import Protocol, TypeAlias

from .aggregation import AggregatedEvaluationResult
from .context import EvaluationExecutionContext
from .models import (
    ComparisonReport,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationObservation,
    EvaluationResult,
    EvaluationRun,
    EvaluatorDescriptor,
)


class Evaluator(Protocol):
    """Replaceable synchronous evaluator boundary."""

    @property
    def descriptor(self) -> EvaluatorDescriptor: ...

    def evaluate(
        self,
        *,
        evaluation_run_id: str,
        case: EvaluationCase,
        observation: EvaluationObservation,
    ) -> EvaluationResult: ...


class AsyncEvaluator(Protocol):
    """Replaceable asynchronous evaluator boundary for model/network-backed scoring."""

    @property
    def descriptor(self) -> EvaluatorDescriptor: ...

    async def evaluate(
        self,
        *,
        evaluation_run_id: str,
        case: EvaluationCase,
        observation: EvaluationObservation,
    ) -> EvaluationResult: ...


EvaluatorLike: TypeAlias = Evaluator | AsyncEvaluator


class EvaluationCaseExecutor(Protocol):
    """Execute one case through a platform/runtime path and return canonical evidence."""

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation: ...


class EvaluationIsolation(Protocol):
    """Reset, set up and tear down isolated state around every case attempt."""

    async def reset_case(self, *, case: EvaluationCase, attempt: EvaluationAttempt) -> None: ...

    async def setup_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
    ) -> EvaluationExecutionContext: ...

    async def teardown_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
        succeeded: bool,
    ) -> None: ...


class EvaluationRepository(Protocol):
    """Persistence boundary for canonical evaluation runs, results, aggregates and comparisons."""

    def save_run(self, run: EvaluationRun) -> None: ...

    def get_run(self, run_id: str) -> EvaluationRun | None: ...

    def save_result(self, result: EvaluationResult) -> None: ...

    def list_results(self, evaluation_run_id: str) -> tuple[EvaluationResult, ...]: ...

    def save_aggregate(self, aggregate: AggregatedEvaluationResult) -> None: ...

    def list_aggregates(
        self,
        evaluation_run_id: str,
        *,
        aggregation_policy_id: str | None = None,
        aggregation_policy_version: str | None = None,
    ) -> tuple[AggregatedEvaluationResult, ...]: ...

    def save_comparison(self, comparison: ComparisonReport) -> None: ...

    def get_comparison(self, current_run_id: str) -> ComparisonReport | None: ...


class EvaluationHistoryRepository(EvaluationRepository, Protocol):
    """Durable/history-capable extension used for runs, case history and trend projection."""

    def list_runs(
        self,
        *,
        suite_id: str | None = None,
        suite_version: str | None = None,
        limit: int | None = 100,
    ) -> tuple[EvaluationRun, ...]: ...

    def list_case_results(
        self,
        *,
        case_id: str,
        evaluator_id: str | None = None,
        limit: int = 100,
    ) -> tuple[EvaluationResult, ...]: ...
