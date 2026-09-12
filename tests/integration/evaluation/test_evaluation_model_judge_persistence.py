from __future__ import annotations

from pathlib import Path

from ai_multi_agent_platform.evaluation import (
    AssertionResult,
    ConfigurationSnapshot,
    EvaluationOutcome,
    EvaluationResult,
    EvaluationRun,
    EvaluatorDescriptor,
    EvaluatorKind,
    SqliteEvaluationRepository,
)


def test_sqlite_roundtrip_preserves_model_judge_identity_and_rubric_evidence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "evaluation.sqlite3"
    repository = SqliteEvaluationRepository(database)
    run = EvaluationRun(
        suite_id="suite.model-judge",
        suite_version="1",
        snapshot=ConfigurationSnapshot(platform_version="0.0.1", platform_commit="test"),
    )
    repository.save_run(run)
    result = EvaluationResult(
        evaluation_run_id=run.run_id,
        case_id="case.model-judge",
        case_version="2",
        evaluator=EvaluatorDescriptor(
            evaluator_id="judge.local-rubric",
            kind=EvaluatorKind.MODEL_JUDGE,
            version="1.2",
            deterministic=False,
            model_config_id="model.judge.local",
            provider_id="provider.local-judge",
            configuration_ref="evaluation/model-judge/rubric-v1",
        ),
        outcome=EvaluationOutcome.PASSED,
        score=0.9,
        assertions=(
            AssertionResult(
                assertion_id="rubric:correctness",
                passed=True,
                message="Evidence supports the answer.",
                expected=0.8,
                actual=0.9,
            ),
        ),
        task_id="task_model_judge",
        run_id="run_model_judge",
        artifact_refs=("artifact_model_judge",),
        telemetry_refs=("telemetry_model_judge",),
    )
    repository.save_result(result)

    reopened = SqliteEvaluationRepository(database)
    loaded = reopened.list_results(run.run_id)

    assert loaded == (result,)
    assert loaded[0].evaluator.kind is EvaluatorKind.MODEL_JUDGE
    assert loaded[0].evaluator.model_config_id == "model.judge.local"
    assert loaded[0].evaluator.provider_id == "provider.local-judge"
    assert loaded[0].evaluator.configuration_ref == "evaluation/model-judge/rubric-v1"
    assert loaded[0].assertions[0].expected == 0.8
    assert loaded[0].assertions[0].actual == 0.9
