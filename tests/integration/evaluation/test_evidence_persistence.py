from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.evaluation import (
    ConfigurationSnapshot,
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationEvidence,
    EvaluationExecutionContext,
    EvaluationObservation,
    EvaluationRun,
    EvidenceEnrichingCaseExecutor,
    SqliteEvaluationRepository,
)


class StaticCaseExecutor:
    def __init__(self, observation: EvaluationObservation) -> None:
        self.observation = observation

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case
        if attempt.attempt_id != execution_context.attempt_id:
            raise ValueError("wrong execution context")
        return self.observation


class StaticEvidenceProvider:
    def __init__(self, evidence: EvaluationEvidence) -> None:
        self.evidence = evidence

    def collect(self, *, task_id: str, run_id: str) -> EvaluationEvidence:
        assert task_id == "task_target"
        assert run_id == "run_target"
        return self.evidence


def test_enriched_evidence_flows_into_result_and_survives_sqlite_restart(
    tmp_path: Path,
) -> None:
    attempt = EvaluationAttempt(
        evaluation_run_id="evaluation_run_test",
        case_id="case",
        case_version="1",
        repetition_index=0,
    )
    evidence = EvaluationEvidence(
        data={"accounting_evidence": {"records": []}},
        metrics={"accounting:run.duration:seconds": 2.5},
        telemetry_refs=(
            "accounting:usage:usage_00000000-0000-0000-0000-000000000001",
            "observability:trace:trace_target",
            "observability:span:span_target",
            "observability:log:local-log:1",
        ),
    )
    wrapped = EvidenceEnrichingCaseExecutor(
        StaticCaseExecutor(
            EvaluationObservation(
                data={"base": True},
                task_id="task_target",
                run_id="run_target",
                telemetry_refs=("existing:telemetry",),
            )
        ),
        StaticEvidenceProvider(evidence),
    )
    case = EvaluationCase(case_id="case", name="Case", version="1")
    observation = asyncio.run(
        wrapped.execute_case(
            case=case,
            attempt=attempt,
            execution_context=EvaluationExecutionContext(attempt_id=attempt.attempt_id),
        )
    )

    assert observation.metrics["accounting:run.duration:seconds"] == 2.5
    assert observation.telemetry_refs == (
        "existing:telemetry",
        *evidence.telemetry_refs,
    )

    result = DeterministicAssertionEvaluator().evaluate(
        evaluation_run_id=attempt.evaluation_run_id,
        case=case,
        observation=observation,
    )
    assert result.telemetry_refs == observation.telemetry_refs

    repository = SqliteEvaluationRepository(tmp_path / "evaluation.sqlite3")
    repository.save_run(
        EvaluationRun(
            run_id=attempt.evaluation_run_id,
            suite_id="suite",
            suite_version="1",
            snapshot=ConfigurationSnapshot(platform_version="0.0.1", platform_commit="test"),
        )
    )
    repository.save_result(result)

    reopened = SqliteEvaluationRepository(tmp_path / "evaluation.sqlite3")
    assert reopened.list_results(attempt.evaluation_run_id)[0].telemetry_refs == (
        observation.telemetry_refs
    )
