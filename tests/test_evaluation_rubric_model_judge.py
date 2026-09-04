from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping

import pytest

from ai_multi_agent_platform.adapters import (
    HttpJsonResponse,
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
)
from ai_multi_agent_platform.contracts import HealthStatus, JsonValue
from ai_multi_agent_platform.evaluation import (
    ConfigurationSnapshot,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    EvaluationObservation,
    EvaluationOutcome,
    EvaluationRunner,
    EvaluationSuite,
    EvaluatorKind,
    InMemoryEvaluationRepository,
    ModelJudgeEvaluator,
    ObservationRubricEvaluator,
    RubricCriterion,
    evaluate_safely,
    parse_evaluation_suite,
)
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    ModelRuntime,
)


class JudgeTransport:
    def __init__(self, response_payload: JsonValue) -> None:
        self.response_payload = response_payload
        self.calls: list[
            tuple[
                str,
                str,
                Mapping[str, str],
                Mapping[str, JsonValue] | None,
                float,
            ]
        ] = []

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, JsonValue] | None,
        timeout_seconds: float,
    ) -> HttpJsonResponse:
        self.calls.append((method, url, headers, payload, timeout_seconds))
        if url.endswith("/models"):
            return HttpJsonResponse(200, {"data": [{"id": "native-judge"}]})
        return HttpJsonResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                self.response_payload,
                                sort_keys=True,
                                allow_nan=False,
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 17},
            },
        )


class StaticExecutor:
    def __init__(self, observation: EvaluationObservation) -> None:
        self.observation = observation

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case, attempt, execution_context
        return self.observation


def _rubric_case() -> EvaluationCase:
    return EvaluationCase(
        case_id="case.rubric",
        name="rubric evaluation",
        version="3",
        rubric=(
            RubricCriterion(
                criterion_id="correctness",
                description="The answer is factually correct.",
                weight=2.0,
                minimum_score=0.8,
            ),
            RubricCriterion(
                criterion_id="clarity",
                description="The answer is clear and understandable.",
                weight=1.0,
                minimum_score=0.7,
            ),
        ),
        tags=("qualitative",),
    )


def _model_runtime(response_payload: JsonValue) -> tuple[ModelRuntime, JudgeTransport]:
    transport = JudgeTransport(response_payload)
    provider = OpenAICompatibleModelProvider(
        OpenAICompatibleProviderConfig(
            provider_id="provider.local-judge",
            base_url="http://127.0.0.1:18080/v1",
            models={"model.judge.local": "native-judge"},
        ),
        transport=transport,
    )
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(
        ModelConfiguration(
            config_id="model.judge.local",
            display_name="Local evaluation judge",
            provider_id="provider.local-judge",
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            capabilities=ModelCapabilities(
                context_window=8_192,
                structured_output=True,
                modalities=("text",),
            ),
        )
    )
    asyncio.run(registry.refresh_health())
    return ModelRuntime(registry), transport


def _model_judge(runtime: ModelRuntime) -> ModelJudgeEvaluator:
    return ModelJudgeEvaluator(
        runtime=runtime,
        model_config_id="model.judge.local",
        configuration_ref="evaluation/model-judge/rubric-v1",
        evaluator_id="judge.local-rubric",
        version="1.2",
    )


def test_observation_rubric_evaluator_applies_weighted_versioned_thresholds() -> None:
    evaluator = ObservationRubricEvaluator()
    result = evaluator.evaluate(
        evaluation_run_id="evaluation_run_rubric",
        case=_rubric_case(),
        observation=EvaluationObservation(
            data={
                "rubric_scores": {
                    "correctness": {"score": 0.9, "explanation": "Correct."},
                    "clarity": {"score": 0.75, "explanation": "Clear."},
                }
            },
            task_id="task_rubric",
            run_id="run_rubric",
            artifact_refs=("artifact_rubric",),
            telemetry_refs=("telemetry_rubric",),
        ),
    )

    assert result.outcome is EvaluationOutcome.PASSED
    assert result.deterministic_pass is True
    assert result.score == pytest.approx(0.85)
    assert result.task_id == "task_rubric"
    assert result.run_id == "run_rubric"
    assert result.artifact_refs == ("artifact_rubric",)
    assert result.telemetry_refs == ("telemetry_rubric",)
    by_id = {item.assertion_id: item for item in result.assertions}
    assert by_id["rubric:correctness"].passed is True
    assert by_id["rubric:correctness"].expected == 0.8
    assert by_id["rubric:correctness"].actual == 0.9
    assert by_id["rubric:clarity"].passed is True


def test_observation_rubric_evaluator_fails_below_case_owned_minimum_score() -> None:
    result = ObservationRubricEvaluator().evaluate(
        evaluation_run_id="evaluation_run_rubric_fail",
        case=_rubric_case(),
        observation=EvaluationObservation(
            data={"rubric_scores": {"correctness": 0.79, "clarity": 1.0}}
        ),
    )

    assert result.outcome is EvaluationOutcome.FAILED
    assert result.deterministic_pass is False
    assert result.score == pytest.approx((0.79 * 2.0 + 1.0) / 3.0)
    failed = [item for item in result.assertions if not item.passed]
    assert [item.assertion_id for item in failed] == ["rubric:correctness"]


def test_rubric_evaluator_failure_is_contained_as_canonical_error_result() -> None:
    result = asyncio.run(
        evaluate_safely(
            ObservationRubricEvaluator(),
            evaluation_run_id="evaluation_run_bad_rubric",
            case=_rubric_case(),
            observation=EvaluationObservation(data={}),
        )
    )

    assert result.outcome is EvaluationOutcome.ERROR
    assert result.error_category == "evaluator_failure"
    assert "rubric_scores" in (result.error_message or "")
    assert result.evaluator.kind is EvaluatorKind.RUBRIC


def test_strict_suite_parser_accepts_and_validates_rubric_minimum_score() -> None:
    suite = parse_evaluation_suite(
        {
            "suite_id": "suite.rubric",
            "name": "rubric suite",
            "version": "1",
            "cases": [
                {
                    "case_id": "case.rubric",
                    "name": "rubric case",
                    "version": "1",
                    "rubric": [
                        {
                            "criterion_id": "quality",
                            "description": "Quality criterion",
                            "weight": 2.0,
                            "minimum_score": 0.75,
                        }
                    ],
                }
            ],
        }
    )

    criterion = suite.cases[0].rubric[0]
    assert criterion.weight == 2.0
    assert criterion.minimum_score == 0.75

    with pytest.raises(ValueError, match="minimum_score"):
        parse_evaluation_suite(
            {
                "suite_id": "suite.invalid-rubric",
                "name": "invalid rubric suite",
                "version": "1",
                "cases": [
                    {
                        "case_id": "case.invalid",
                        "name": "invalid",
                        "version": "1",
                        "rubric": [
                            {
                                "criterion_id": "quality",
                                "description": "Quality criterion",
                                "minimum_score": 1.1,
                            }
                        ],
                    }
                ],
            }
        )


def test_model_judge_uses_canonical_model_runtime_and_records_identity() -> None:
    runtime, transport = _model_runtime(
        {
            "criteria": [
                {
                    "criterion_id": "correctness",
                    "score": 0.9,
                    "explanation": "Evidence supports the answer.",
                },
                {
                    "criterion_id": "clarity",
                    "score": 0.75,
                    "explanation": "The answer is understandable.",
                },
            ]
        }
    )
    evaluator = _model_judge(runtime)
    observation = EvaluationObservation(
        data={"answer": "42", "reference": "42"},
        task_id="task_judge",
        run_id="run_judge",
        artifact_refs=("artifact_judge",),
        telemetry_refs=("telemetry_judge",),
    )

    result = asyncio.run(
        evaluator.evaluate(
            evaluation_run_id="evaluation_run_judge",
            case=_rubric_case(),
            observation=observation,
        )
    )

    assert result.outcome is EvaluationOutcome.PASSED
    assert result.deterministic_pass is None
    assert result.score == pytest.approx(0.85)
    assert result.evaluator.kind is EvaluatorKind.MODEL_JUDGE
    assert result.evaluator.deterministic is False
    assert result.evaluator.model_config_id == "model.judge.local"
    assert result.evaluator.provider_id == "provider.local-judge"
    assert result.evaluator.configuration_ref == "evaluation/model-judge/rubric-v1"
    assert result.evaluator.version == "1.2"
    assert result.task_id == "task_judge"
    assert result.run_id == "run_judge"

    generation_payload = transport.calls[-1][3]
    assert generation_payload is not None
    assert generation_payload["model"] == "native-judge"
    response_format = generation_payload["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    assert generation_payload["temperature"] == 0.0


def test_model_judge_malformed_output_is_contained_and_keeps_judge_metadata() -> None:
    runtime, _ = _model_runtime(
        {
            "criteria": [
                {
                    "criterion_id": "unknown",
                    "score": 0.9,
                    "explanation": "Not a declared criterion.",
                }
            ]
        }
    )
    evaluator = _model_judge(runtime)

    result = asyncio.run(
        evaluate_safely(
            evaluator,
            evaluation_run_id="evaluation_run_bad_judge",
            case=_rubric_case(),
            observation=EvaluationObservation(data={"answer": "42"}),
        )
    )

    assert result.outcome is EvaluationOutcome.ERROR
    assert result.error_category == "evaluator_failure"
    assert result.evaluator.kind is EvaluatorKind.MODEL_JUDGE
    assert result.evaluator.model_config_id == "model.judge.local"
    assert result.evaluator.provider_id == "provider.local-judge"
    assert result.evaluator.configuration_ref == "evaluation/model-judge/rubric-v1"


def test_model_judge_does_not_call_provider_when_case_has_no_rubric() -> None:
    runtime, transport = _model_runtime({"criteria": []})
    evaluator = _model_judge(runtime)
    calls_before = len(transport.calls)

    result = asyncio.run(
        evaluator.evaluate(
            evaluation_run_id="evaluation_run_no_rubric",
            case=EvaluationCase(case_id="case.no-rubric", name="no rubric", version="1"),
            observation=EvaluationObservation(data={"answer": "anything"}),
        )
    )

    assert result.outcome is EvaluationOutcome.PASSED
    assert result.score == 1.0
    assert len(transport.calls) == calls_before


def test_evaluation_runner_executes_async_model_judge_and_persists_attempt_metadata() -> None:
    runtime, _ = _model_runtime(
        {
            "criteria": [
                {
                    "criterion_id": "correctness",
                    "score": 0.95,
                    "explanation": "Correct.",
                },
                {
                    "criterion_id": "clarity",
                    "score": 0.8,
                    "explanation": "Clear.",
                },
            ]
        }
    )
    repository = InMemoryEvaluationRepository()
    runner = EvaluationRunner(
        repository=repository,
        executor=StaticExecutor(
            EvaluationObservation(
                data={"answer": "42"},
                task_id="task_async_judge",
                run_id="run_async_judge",
            )
        ),
        evaluators=(_model_judge(runtime),),
    )
    suite = EvaluationSuite(
        suite_id="suite.async-judge",
        name="async judge suite",
        version="1",
        cases=(_rubric_case(),),
    )

    summary = asyncio.run(
        runner.run_suite(
            suite=suite,
            snapshot=ConfigurationSnapshot(platform_version="0.0.1", platform_commit="test"),
            repetitions=1,
            seed=23,
        )
    )

    assert len(summary.results) == 1
    result = summary.results[0]
    assert result.outcome is EvaluationOutcome.PASSED
    assert result.evaluator.kind is EvaluatorKind.MODEL_JUDGE
    assert result.attempt_id is not None
    assert result.repetition_index == 0
    assert result.seed == 23
    assert result.task_id == "task_async_judge"
    assert result.run_id == "run_async_judge"
    assert repository.list_results(summary.run.run_id) == summary.results
