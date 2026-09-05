"""First-party Agent/Model/Capability evidence projection for Evaluation."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from ai_multi_agent_platform.agents.models import AgentRunRecord
from ai_multi_agent_platform.contracts.types import JsonValue

from .context import EvaluationExecutionContext
from .contracts import EvaluationCaseExecutor
from .models import EvaluationAttempt, EvaluationCase, EvaluationObservation

_AGENT_BEHAVIOR_KEY = "agent_behavior"


class AgentRunReader(Protocol):
    """Minimal source-owned AgentRun read boundary needed by Evaluation."""

    def list_agent_runs(self, run_id: str | None = None) -> tuple[AgentRunRecord, ...]: ...


def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _unique_optional(values: tuple[str | None, ...]) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if value is not None}))


class AgentRunEvidenceCaseExecutor:
    """Enrich any canonical case executor with source-owned AgentRun evidence.

    The decorator never invents Agent/model/tool facts. It reads exact AgentRun records
    associated with the canonical Run produced by the wrapped executor. Singular
    top-level model/provider fields are populated only when the run has one unambiguous
    selected identity; multi-agent detail remains available in ``agent_behavior.runs``.
    """

    def __init__(self, executor: EvaluationCaseExecutor, agents: AgentRunReader) -> None:
        self._executor = executor
        self._agents = agents

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        observation = await self._executor.execute_case(
            case=case,
            attempt=attempt,
            execution_context=execution_context,
        )
        if observation.run_id is None:
            return observation

        records = tuple(
            sorted(
                self._agents.list_agent_runs(observation.run_id),
                key=lambda item: item.agent_run_id,
            )
        )
        if not records:
            return observation
        if _AGENT_BEHAVIOR_KEY in observation.data:
            raise ValueError("evaluation observation data must not shadow agent_behavior evidence")

        model_ids = _unique_optional(tuple(record.selected_model_config_id for record in records))
        provider_ids = _unique_optional(tuple(record.selected_provider_id for record in records))
        selected_model = observation.selected_model_config_id
        selected_provider = observation.selected_provider_id
        if selected_model is not None and model_ids and model_ids != (selected_model,):
            raise ValueError("AgentRun model evidence conflicts with existing evaluation observation")
        if selected_provider is not None and provider_ids and provider_ids != (selected_provider,):
            raise ValueError("AgentRun provider evidence conflicts with existing evaluation observation")
        if selected_model is None and len(model_ids) == 1:
            selected_model = model_ids[0]
        if selected_provider is None and len(provider_ids) == 1:
            selected_provider = provider_ids[0]

        capability_refs = _unique(
            (
                *observation.capability_refs,
                *(capability for record in records for capability in record.capability_ids),
            )
        )
        artifact_refs = _unique(
            (
                *observation.artifact_refs,
                *(artifact for record in records for artifact in record.artifact_ids),
            )
        )
        run_payloads: list[JsonValue] = []
        for record in records:
            run_payloads.append(
                {
                    "agent_run_id": record.agent_run_id,
                    "agent_id": record.agent.agent_id,
                    "agent_revision": record.agent.revision,
                    "team_id": None if record.team is None else record.team.team_id,
                    "team_revision": None if record.team is None else record.team.revision,
                    "status": record.status.value,
                    "selected_model_config_id": record.selected_model_config_id,
                    "selected_provider_id": record.selected_provider_id,
                    "capability_ids": list(record.capability_ids),
                    "capability_versions": dict(record.capability_versions),
                    "orchestrator_adapter_id": record.orchestrator_adapter_id,
                    "orchestrator_runtime_ref": record.orchestrator_runtime_ref,
                    "artifact_ids": list(record.artifact_ids),
                    "result_ids": list(record.result_ids),
                    "model_call_refs": list(record.model_call_refs),
                    "tool_invocation_refs": list(record.tool_invocation_refs),
                    "error": record.error,
                    "telemetry": dict(record.telemetry),
                    "verification_context": dict(record.verification_context),
                }
            )

        return replace(
            observation,
            data={**observation.data, _AGENT_BEHAVIOR_KEY: {"runs": run_payloads}},
            artifact_refs=artifact_refs,
            selected_model_config_id=selected_model,
            selected_provider_id=selected_provider,
            capability_refs=capability_refs,
        )


__all__ = ["AgentRunEvidenceCaseExecutor", "AgentRunReader"]
