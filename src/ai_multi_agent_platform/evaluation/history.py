"""Historical evaluation projections built on the replaceable history repository."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .contracts import EvaluationHistoryRepository
from .models import EvaluationOutcome, MetricResult


@dataclass(frozen=True, slots=True)
class EvaluationTrendPoint:
    """One historical case/evaluator result enriched with its configuration snapshot identity."""

    evaluation_run_id: str
    suite_id: str
    suite_version: str
    case_id: str
    case_version: str
    evaluator_id: str
    evaluator_version: str
    outcome: EvaluationOutcome
    deterministic_pass: bool | None
    score: float | None
    metrics: tuple[MetricResult, ...]
    snapshot_id: str
    platform_version: str
    platform_commit: str | None
    created_at: datetime


class EvaluationHistoryService:
    """Query restart-safe run history and project case trends without backend-specific SQL."""

    def __init__(self, repository: EvaluationHistoryRepository) -> None:
        self._repository = repository

    def case_trend(
        self,
        *,
        case_id: str,
        evaluator_id: str | None = None,
        limit: int = 100,
    ) -> tuple[EvaluationTrendPoint, ...]:
        results = self._repository.list_case_results(
            case_id=case_id,
            evaluator_id=evaluator_id,
            limit=limit,
        )
        points: list[EvaluationTrendPoint] = []
        for result in results:
            run = self._repository.get_run(result.evaluation_run_id)
            if run is None:
                raise ValueError(
                    "evaluation history is inconsistent: result references missing run "
                    f"{result.evaluation_run_id}"
                )
            points.append(
                EvaluationTrendPoint(
                    evaluation_run_id=run.run_id,
                    suite_id=run.suite_id,
                    suite_version=run.suite_version,
                    case_id=result.case_id,
                    case_version=result.case_version,
                    evaluator_id=result.evaluator.evaluator_id,
                    evaluator_version=result.evaluator.version,
                    outcome=result.outcome,
                    deterministic_pass=result.deterministic_pass,
                    score=result.score,
                    metrics=result.metrics,
                    snapshot_id=run.snapshot.snapshot_id,
                    platform_version=run.snapshot.platform_version,
                    platform_commit=run.snapshot.platform_commit,
                    created_at=result.created_at,
                )
            )
        return tuple(points)
