"""Optional model-backed rubric evaluator using the canonical ModelRuntime."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from ai_multi_agent_platform.contracts import JsonValue, OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.models import (
    CanonicalModelRequest,
    ModelGenerationParameters,
    ModelMessage,
    ModelRole,
    ModelRuntime,
    StructuredResponseExpectation,
    StructuredResponseKind,
)

from .models import (
    EvaluationCase,
    EvaluationObservation,
    EvaluationResult,
    EvaluatorDescriptor,
    EvaluatorKind,
)
from .rubric import build_rubric_result

_SYSTEM_INSTRUCTION = (
    "You are an evaluation rubric scorer. Score only the supplied criteria against the supplied "
    "observation evidence. Do not invent missing evidence. Return exactly the requested structured "
    "JSON. Every criterion score must be between 0.0 and 1.0."
)

_RUBRIC_RESPONSE_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion_id": {"type": "string"},
                    "score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "explanation": {"type": "string"},
                },
                "required": ["criterion_id", "score", "explanation"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["criteria"],
    "additionalProperties": False,
}


class ModelJudgeEvaluator:
    """Optional non-deterministic rubric evaluator over the provider-neutral model runtime.

    The exact canonical model configuration, provider and evaluator configuration
    reference are recorded in the result descriptor. Pass/fail thresholds remain
    case-owned rubric configuration; the model returns scores and explanations,
    not hidden acceptance decisions.
    """

    def __init__(
        self,
        *,
        runtime: ModelRuntime,
        model_config_id: str,
        configuration_ref: str,
        evaluator_id: str = "reference.model-judge",
        version: str = "1.0",
    ) -> None:
        if not configuration_ref.strip():
            raise ValueError("model judge configuration_ref must not be blank")
        model_config = runtime.registry.get_model(model_config_id)
        if not model_config.capabilities.structured_output:
            raise ValueError("model judge model configuration must support structured output")
        self._runtime = runtime
        self.descriptor = EvaluatorDescriptor(
            evaluator_id=evaluator_id,
            kind=EvaluatorKind.MODEL_JUDGE,
            version=version,
            deterministic=False,
            model_config_id=model_config.config_id,
            provider_id=model_config.provider_id,
            configuration_ref=configuration_ref,
        )

    async def evaluate(
        self,
        *,
        evaluation_run_id: str,
        case: EvaluationCase,
        observation: EvaluationObservation,
    ) -> EvaluationResult:
        if not case.rubric:
            return build_rubric_result(
                descriptor=self.descriptor,
                evaluation_run_id=evaluation_run_id,
                case=case,
                observation=observation,
                scores={},
            )

        request = CanonicalModelRequest(
            request_id=new_id("model_request"),
            context=OperationContext(
                correlation_id=f"evaluation-model-judge:{evaluation_run_id}:{case.case_id}"
            ),
            system_instruction=_SYSTEM_INSTRUCTION,
            messages=(ModelMessage.text(ModelRole.USER, _judge_payload(case, observation)),),
            response=StructuredResponseExpectation(
                kind=StructuredResponseKind.JSON_SCHEMA,
                schema_name="evaluation_rubric_scores",
                json_schema=_RUBRIC_RESPONSE_SCHEMA,
                strict=True,
            ),
            generation=ModelGenerationParameters(temperature=0.0),
            model_config_id=self.descriptor.model_config_id,
            task_id=observation.task_id,
            run_id=observation.run_id,
            routing_requirements={"structured_output": True},
        )
        response = await self._runtime.generate_canonical(request)
        if response.model_config_id != self.descriptor.model_config_id:
            raise ValueError("model judge response came from an unexpected model configuration")
        scores = _parse_scores(response.structured_output)
        return build_rubric_result(
            descriptor=self.descriptor,
            evaluation_run_id=evaluation_run_id,
            case=case,
            observation=observation,
            scores=scores,
        )


def _judge_payload(case: EvaluationCase, observation: EvaluationObservation) -> str:
    payload: dict[str, JsonValue] = {
        "case": {
            "case_id": case.case_id,
            "case_version": case.version,
            "name": case.name,
            "input_template": dict(case.input_template),
            "criteria": [
                {
                    "criterion_id": criterion.criterion_id,
                    "description": criterion.description,
                }
                for criterion in case.rubric
            ],
        },
        "observation": {
            "data": dict(observation.data),
            "metrics": dict(observation.metrics),
            "task_id": observation.task_id,
            "run_id": observation.run_id,
            "artifact_refs": list(observation.artifact_refs),
            "telemetry_refs": list(observation.telemetry_refs),
            "selected_model_config_id": observation.selected_model_config_id,
            "selected_provider_id": observation.selected_provider_id,
            "capability_refs": list(observation.capability_refs),
            "event_types": list(observation.event_types),
        },
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _parse_scores(value: JsonValue) -> dict[str, tuple[float, str]]:
    if not isinstance(value, Mapping):
        raise ValueError("model judge must return a structured JSON object")
    if set(value) != {"criteria"}:
        raise ValueError("model judge response must contain only the criteria field")
    raw_criteria = value.get("criteria")
    if not isinstance(raw_criteria, Sequence) or isinstance(raw_criteria, str | bytes):
        raise ValueError("model judge criteria must be an array")

    scores: dict[str, tuple[float, str]] = {}
    for raw_item in raw_criteria:
        if not isinstance(raw_item, Mapping):
            raise ValueError("model judge criterion entries must be objects")
        if set(raw_item) != {"criterion_id", "score", "explanation"}:
            raise ValueError(
                "model judge criterion entries require criterion_id, score and explanation only"
            )
        criterion_id = raw_item.get("criterion_id")
        raw_score = raw_item.get("score")
        explanation = raw_item.get("explanation")
        if not isinstance(criterion_id, str) or not criterion_id.strip():
            raise ValueError("model judge criterion_id must be a non-blank string")
        if criterion_id in scores:
            raise ValueError(f"model judge returned duplicate criterion: {criterion_id}")
        if isinstance(raw_score, bool) or not isinstance(raw_score, int | float):
            raise ValueError(f"model judge score for {criterion_id} must be numeric")
        score = float(raw_score)
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"model judge score for {criterion_id} must be between 0.0 and 1.0")
        if not isinstance(explanation, str):
            raise ValueError(f"model judge explanation for {criterion_id} must be a string")
        scores[criterion_id] = (score, explanation)
    return scores


__all__ = ["ModelJudgeEvaluator"]
