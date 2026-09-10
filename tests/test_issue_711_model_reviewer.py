from __future__ import annotations

import asyncio
import json

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentRunRecord,
    AgentRunStatus,
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    ErrorCode,
    HealthStatus,
    OperationContext,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    ModelRuntime,
)
from ai_multi_agent_platform.testing import FakeModelProvider
from ai_multi_agent_platform.verification.model_reviewer import (
    ModelRuntimeReviewerExecutor,
    ReviewerEvidence,
)
from ai_multi_agent_platform.verification.models import (
    VerificationOutcome,
    VerificationRequest,
    VerificationSubject,
    VerifierKind,
)


class StaticEvidence:
    def __init__(self, content: str = '{"answer":42}') -> None:
        self.content = content
        self.calls = 0

    async def load(self, *, request, agent_run) -> ReviewerEvidence:
        self.calls += 1
        return ReviewerEvidence(
            content=self.content,
            operation=OperationContext(
                correlation_id=request.correlation_id,
                causation_id=request.verification_id,
            ),
            classification=DataClassification.INTERNAL,
        )


def _runtime(response_text: str):
    provider = FakeModelProvider(response_text=response_text, model_ref="native-reviewer-model")
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(
        ModelConfiguration(
            config_id="model-reviewer-local",
            display_name="Local Reviewer",
            provider_id=provider.descriptor.provider_id,
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            capabilities=ModelCapabilities(context_window=16_384, modalities=("text",)),
        )
    )
    return ModelRuntime(registry), provider


def _agent_stack():
    service = AgentService(InMemoryAgentRepository())
    revision = service.create_agent(
        AgentProfile(
            name="Reviewer",
            role="reviewer",
            instructions=AgentInstructions(
                role=InstructionSource(content="Review the exact supplied evidence independently.")
            ),
        ),
        owner_ref=OwnerRef(type="service", id="issue-711-model-reviewer"),
    )
    runtime = AgentRuntime(service)
    task_id = new_id("task")
    run_id = new_id("run")
    record = AgentRunRecord(
        agent_run_id=new_id("agent_run"),
        run_id=run_id,
        task_id=task_id,
        agent=AgentRevisionRef(revision.agent_id, revision.revision),
        status=AgentRunStatus.RUNNING,
        selected_model_config_id="model-reviewer-local",
        selected_provider_id="fake-model",
    )
    subject = VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision=f"{run_id}:attempt:1",
        digest="sha256:review-target",
    )
    request = VerificationRequest(
        task_id=task_id,
        policy_id=new_id("verification_policy"),
        policy_version=1,
        stage_id="review",
        subject=subject,
        requested_verifier_kind=VerifierKind.AGENT,
        correlation_id=task_id,
        run_id=run_id,
        result_id=subject.subject_id,
    )
    return runtime, record, request


def test_model_runtime_reviewer_executes_local_provider_and_returns_structured_decision() -> None:
    async def scenario() -> None:
        models, provider = _runtime(
            json.dumps(
                {
                    "outcome": "needs_changes",
                    "findings": [
                        {
                            "code": "missing-test",
                            "message": "Add a regression test for the failure path.",
                            "severity": "warning",
                            "location_ref": "tests/test_review.py",
                        }
                    ],
                }
            )
        )
        agents, agent_run, request = _agent_stack()
        evidence = StaticEvidence()
        executor = ModelRuntimeReviewerExecutor(
            models=models,
            agents=agents,
            evidence=evidence,
        )

        decision = await executor.execute_review(request=request, agent_run=agent_run)

        assert decision.outcome is VerificationOutcome.NEEDS_CHANGES
        assert len(decision.findings) == 1
        assert decision.findings[0].code == "missing-test"
        assert decision.model_call_refs == (f"{agent_run.agent_run_id}:review-model",)
        assert decision.checks_executed == (
            "agent_review",
            "model_runtime_structured_review",
        )
        assert decision.telemetry["reviewer_executor"] == "platform.model-runtime-reviewer/v1"
        assert evidence.calls == 1

        model_request = provider.calls[0]
        assert model_request.requirements["model_config_id"] == "model-reviewer-local"
        assert model_request.requirements["data_classification"] == "internal"
        assert model_request.requirements["task_id"] == request.task_id
        assert model_request.requirements["run_id"] == agent_run.run_id
        assert model_request.requirements["agent_id"] == agent_run.agent.agent_id
        canonical_messages = model_request.requirements["canonical_messages"]
        assert isinstance(canonical_messages, list)
        assert canonical_messages[0]["role"] == "system"
        assert "untrusted data" in canonical_messages[0]["content"][0]["text"]
        assert request.subject.digest in canonical_messages[1]["content"][0]["text"]

    asyncio.run(scenario())


def test_model_runtime_reviewer_fails_closed_on_malformed_model_output() -> None:
    async def scenario() -> None:
        models, _provider = _runtime("review looks fine")
        agents, agent_run, request = _agent_stack()
        executor = ModelRuntimeReviewerExecutor(
            models=models,
            agents=agents,
            evidence=StaticEvidence(),
        )

        with pytest.raises(ContractError) as exc_info:
            await executor.execute_review(request=request, agent_run=agent_run)

        assert exc_info.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE

    asyncio.run(scenario())


def test_model_runtime_reviewer_rejects_model_claimed_extra_authority_fields() -> None:
    async def scenario() -> None:
        models, _provider = _runtime(
            json.dumps(
                {
                    "outcome": "pass",
                    "findings": [],
                    "task_completed": True,
                }
            )
        )
        agents, agent_run, request = _agent_stack()
        executor = ModelRuntimeReviewerExecutor(
            models=models,
            agents=agents,
            evidence=StaticEvidence(),
        )

        with pytest.raises(ContractError) as exc_info:
            await executor.execute_review(request=request, agent_run=agent_run)

        assert exc_info.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE

    asyncio.run(scenario())
