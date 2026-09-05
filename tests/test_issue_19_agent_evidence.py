from __future__ import annotations

import asyncio

from ai_multi_agent_platform.agents import AgentRevisionRef, AgentRunRecord, AgentRunStatus
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.evaluation import (
    AgentRunEvidenceCaseExecutor,
    ComparisonOperator,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    EvaluationObservation,
    EvaluationOutcome,
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


class StaticAgentRuns:
    def __init__(self, records: tuple[AgentRunRecord, ...]) -> None:
        self.records = records

    def list_agent_runs(self, run_id: str | None = None) -> tuple[AgentRunRecord, ...]:
        if run_id is None:
            return self.records
        return tuple(record for record in self.records if record.run_id == run_id)


def test_agent_run_evidence_projects_model_provider_capability_and_tool_behavior() -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        run_id = new_id("run")
        record = AgentRunRecord(
            agent_run_id=new_id("agent_run"),
            task_id=task_id,
            run_id=run_id,
            agent=AgentRevisionRef(agent_id=new_id("agent"), revision=3),
            status=AgentRunStatus.SUCCEEDED,
            selected_model_config_id="model.local.qwen",
            selected_provider_id="provider.local",
            capability_ids=("capability.shell",),
            capability_versions={"capability.shell": "2"},
            orchestrator_adapter_id="reference",
            orchestrator_runtime_ref="reference@1",
            model_call_refs=("model-call-1",),
            tool_invocation_refs=("tool-call-1",),
        )
        wrapped = AgentRunEvidenceCaseExecutor(
            StaticExecutor(EvaluationObservation(task_id=task_id, run_id=run_id)),
            StaticAgentRuns((record,)),
        )
        case = EvaluationCase(
            case_id="case.agent-evidence",
            name="agent evidence",
            version="1",
            assertions=(
                DeterministicAssertion(
                    "model",
                    "behavior.selected_model_config_id",
                    ComparisonOperator.EQ,
                    expected="model.local.qwen",
                ),
                DeterministicAssertion(
                    "provider",
                    "behavior.selected_provider_id",
                    ComparisonOperator.EQ,
                    expected="provider.local",
                ),
                DeterministicAssertion(
                    "capability",
                    "behavior.capability_refs",
                    ComparisonOperator.CONTAINS,
                    expected="capability.shell",
                ),
                DeterministicAssertion(
                    "tool",
                    "agent_behavior.runs.0.tool_invocation_refs",
                    ComparisonOperator.CONTAINS,
                    expected="tool-call-1",
                ),
                DeterministicAssertion(
                    "agent-revision",
                    "agent_behavior.runs.0.agent_revision",
                    ComparisonOperator.EQ,
                    expected=3,
                ),
            ),
        )
        attempt = EvaluationAttempt(
            evaluation_run_id="evaluation_run_agent_evidence",
            case_id=case.case_id,
            case_version=case.version,
            repetition_index=0,
        )
        observation = await wrapped.execute_case(
            case=case,
            attempt=attempt,
            execution_context=EvaluationExecutionContext(attempt_id=attempt.attempt_id),
        )
        result = DeterministicAssertionEvaluator().evaluate(
            evaluation_run_id=attempt.evaluation_run_id,
            case=case,
            observation=observation,
        )

        assert observation.selected_model_config_id == "model.local.qwen"
        assert observation.selected_provider_id == "provider.local"
        assert observation.capability_refs == ("capability.shell",)
        assert result.outcome is EvaluationOutcome.PASSED
        assert all(assertion.passed for assertion in result.assertions)

    asyncio.run(scenario())
