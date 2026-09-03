"""Replaceable evaluation, execution-isolation and persistence contracts."""

from __future__ import annotations

from typing import Protocol

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
    """Replaceable evaluator boundary shared by deterministic and scored evaluation."""

    @property
    def descriptor(self) -> EvaluatorDescriptor: ...

    def evaluate(
        self,
        *,
        evaluation_run_id: str,
        case: EvaluationCase,
        observation: EvaluationObservation,
    ) -> EvaluationResult: ...


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
    """Persistence boundary for canonical evaluation runs, results and comparisons."""

    def save_run(self, run: EvaluationRun) -> None: ...

    def get_run(self, run_id: str) -> EvaluationRun | None: ...

    def save_result(self, result: EvaluationResult) -> None: ...

    def list_results(self, evaluation_run_id: str) -> tuple[EvaluationResult, ...]: ...

    def save_comparison(self, comparison: ComparisonReport) -> None: ...

    def get_comparison(self, current_run_id: str) -> ComparisonReport | None: ...
