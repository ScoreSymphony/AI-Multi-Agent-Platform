from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, HealthStatus
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    ModelRuntime,
)
from ai_multi_agent_platform.testing import FakeModelProvider
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    CompletionState,
    ProducerIdentity,
    ReviewerIndependence,
    VerificationCompletionAuthority,
    VerificationEvidenceContext,
    VerificationOutcome,
    VerificationPolicy,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)
from ai_multi_agent_platform.verification.agent_workflow import (
    AutomaticReviewerWorkflow,
    ConfiguredReviewerResolver,
    ReviewerAssignment,
)
from ai_multi_agent_platform.verification.reference_reviewer import (
    ModelRuntimeReviewerExecutor,
    ReviewerSubjectInput,
)


class Evidence:
    def __init__(self, context: VerificationEvidenceContext) -> None:
        self.context = context

    async def resolve_subject(self, *, task_id, subject_type, subject_id):
        assert task_id == self.context.task_id
        assert subject_type == self.context.subject.subject_type
        assert subject_id == self.context.subject.subject_id
        return self.context.subject

    async def resolve_context(self, *, task_id, subject_type, subject_id):
        await self.resolve_subject(
            task_id=task_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
        return self.context

    async def validate_evidence_artifacts(self, *, task_id, artifact_ids):
        assert task_id == self.context.task_id
        return artifact_ids


class ExactInputProvider:
    def __init__(self, review_input: ReviewerSubjectInput) -> None:
        self.review_input = review_input
        self.calls = 0

    async def load(self, *, request, agent_run):
        assert agent_run.task_id == request.task_id
        self.calls += 1
        return self.review_input


def _profile(name: str, role: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role=role,
        instructions=AgentInstructions(
            role=InstructionSource(content=f"Act as the independent {role}."),
        ),
    )


def _stack(response_text: str):
    model_provider = FakeModelProvider(
        response_text=response_text,
        model_ref="native-local-reviewer",
    )
    model_registry = ModelRegistry()
    model_registry.register_provider(model_provider)
    model_registry.register_model(
        ModelConfiguration(
            config_id="local-review-model",
            display_name="Local review model",
            provider_id=model_provider.descriptor.provider_id,
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            capabilities=ModelCapabilities(
                context_window=8_192,
                structured_output=True,
                modalities=("text",),
            ),
        )
    )
    models = ModelRuntime(model_registry)

    service = AgentService(InMemoryAgentRepository())
    producer = service.create_agent(
        _profile("Developer", "developer"),
        owner_ref=OwnerRef(type="service", id="issue-711-local"),
    )
    reviewer = service.create_agent(
        _profile("Reviewer", "reviewer"),
        owner_ref=OwnerRef(type="service", id="issue-711-local"),
    )
    agents = AgentRuntime(service, model_registry=model_registry)

    verification = VerificationService()
    policy = verification.register_policy(
        VerificationPolicy(
            name="local automatic review",
            stages=(VerificationStage("review", VerifierKind.AGENT),),
            independence=ReviewerIndependence(
                producer_agent_must_differ=True,
                agent_reviewer_must_be_read_only=True,
            ),
        )
    )
    task_id = new_id("task")
    producer_run_id = new_id("run")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="run-attempt:1",
        digest="sha256:local-review-subject",
    )
    evidence = Evidence(
        VerificationEvidenceContext(
            task_id=task_id,
            subject=subject,
            run_id=producer_run_id,
            project_id=None,
            capability_ids=(),
            producer=ProducerIdentity(
                actor_ref=f"agent:{producer.agent_id}@{producer.revision}",
                agent_id=producer.agent_id,
                agent_revision=producer.revision,
            ),
        )
    )
    completion = VerificationCompletionAuthority(verification)
    runtime = CanonicalVerificationRuntime(completion, evidence)
    request = completion.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        run_id=producer_run_id,
        result_id=subject.subject_id,
        producer=evidence.context.producer,
        correlation_id="issue-711-local-review",
    )
    inputs = ExactInputProvider(
        ReviewerSubjectInput(
            subject=subject,
            content="The produced answer is 42 and includes its supporting evidence.",
        )
    )
    executor = ModelRuntimeReviewerExecutor(
        agents=agents,
        models=models,
        inputs=inputs,
    )
    workflow = AutomaticReviewerWorkflow(
        runtime=runtime,
        completion=completion,
        agents=agents,
        resolver=ConfiguredReviewerResolver(
            {
                (policy.policy_id, policy.version, "review"): ReviewerAssignment(
                    agent_id=reviewer.agent_id,
                    agent_revision=reviewer.revision,
                )
            }
        ),
        executor=executor,
    )
    return workflow, model_provider, agents, inputs, request


def test_local_modelruntime_reviewer_executes_and_records_canonical_result() -> None:
    async def scenario() -> None:
        workflow, provider, agents, inputs, request = _stack(
            '{"outcome":"pass","findings":[{"code":"verified",'
            '"message":"Evidence supports the result.","severity":"info"}]}'
        )

        result = await workflow.run_request(request.verification_id)

        assert result.completion.state is CompletionState.ACCEPTED
        assert result.latest.verification_result is not None
        assert result.latest.verification_result.outcome is VerificationOutcome.PASS
        assert result.latest.verification_result.findings[0].code == "verified"
        assert inputs.calls == 1
        assert len(provider.calls) == 1
        model_request = provider.calls[0]
        assert model_request.requirements["model_config_id"] == "local-review-model"
        assert model_request.requirements["task_id"] == request.task_id
        assert model_request.requirements["agent_id"] == result.latest.reviewer_run.agent.agent_id
        assert request.subject.digest in "\n".join(model_request.messages)
        assert "The produced answer is 42" in "\n".join(model_request.messages)

        reviewer_runs = agents.service.repository.list_agent_runs()
        assert len(reviewer_runs) == 1
        assert reviewer_runs[0].selected_model_config_id == "local-review-model"
        assert reviewer_runs[0].selected_provider_id == provider.descriptor.provider_id
        assert reviewer_runs[0].model_call_refs == (
            f"{reviewer_runs[0].agent_run_id}:review-model",
        )

    asyncio.run(scenario())


def test_local_reviewer_rejects_stale_subject_before_model_invocation() -> None:
    async def scenario() -> None:
        workflow, provider, agents, inputs, request = _stack('{"outcome":"pass","findings":[]}')
        inputs.review_input = ReviewerSubjectInput(
            subject=VerificationSubject(
                subject_type="result",
                subject_id=request.subject.subject_id,
                revision="stale-revision",
                digest="sha256:stale",
            ),
            content="Stale content",
        )

        with pytest.raises(ContractError) as exc_info:
            await workflow.run_request(request.verification_id)

        assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
        assert provider.calls == []
        runs = agents.service.repository.list_agent_runs()
        assert len(runs) == 1
        assert runs[0].status.value == "failed"

    asyncio.run(scenario())


def test_local_reviewer_malformed_output_fails_closed() -> None:
    async def scenario() -> None:
        workflow, provider, agents, _inputs, request = _stack("not-json")

        with pytest.raises(ContractError) as exc_info:
            await workflow.run_request(request.verification_id)

        assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
        assert len(provider.calls) == 1
        runs = agents.service.repository.list_agent_runs()
        assert len(runs) == 1
        assert runs[0].status.value == "failed"
        assert (
            workflow.status_for(request.verification_id).completion.state is CompletionState.WAITING
        )

    asyncio.run(scenario())
