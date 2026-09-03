"""Replaceable evaluation and persistence contracts."""

from __future__ import annotations

from typing import Protocol

from .models import (
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


class EvaluationRepository(Protocol):
    """Persistence boundary for canonical evaluation runs and results."""

    def save_run(self, run: EvaluationRun) -> None: ...

    def get_run(self, run_id: str) -> EvaluationRun | None: ...

    def save_result(self, result: EvaluationResult) -> None: ...

    def list_results(self, evaluation_run_id: str) -> tuple[EvaluationResult, ...]: ...
