from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.agents.models import AgentExecutionSpec, OrchestratorMapping
from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    HealthStatus,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    RoutingRequirements,
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
from ai_multi_agent_platform.verification import agent_repair as agent_repair_module
from ai_multi_agent_platform.verification import agent_workflow as agent_workflow_module
from ai_multi_agent_platform.verification import output_workflow as output_workflow_module
from ai_multi_agent_platform.verification import reviewer_agent as reviewer_agent_module
from ai_multi_agent_platform.verification import reviewer_routing as reviewer_routing_module
from ai_multi_agent_platform.verification.agent_workflow import (
    AutomaticReviewerWorkflow,
    ConfiguredReviewerResolver,
    ReviewerAssignment,
    ReviewerExecutionDecision,
    ReviewerRuntimeOptions,
)


class _ProviderA(FakeModelProvider):
    descriptor = ProviderDescriptor(
        provider_id="automatic-review-provider-a",
        provider_type="model",
        supported_operations=("generate",),
        capabilities=(
            Capability(
                name="model.text",
                kind=CapabilityKind.MODEL,
                supported_operations=("generate",),
                modalities=("text",),
            ),
        ),
        health=HealthStatus.HEALTHY,
        available=True,
    )


class _ProviderB(FakeModelProvider):
    descriptor = ProviderDescriptor(
        provider_id="automatic-review-provider-b",
        provider_type="model",
        supported_operations=("generate",),
        capabilities=(
            Capability(
                name="model.text",
                kind=CapabilityKind.MODEL,
                supported_operations=("generate",),
                modalities=("text",),
            ),
        ),
        health=HealthStatus.HEALTHY,
        available=True,
    )


class _Evidence:
    def __init__(self, context: VerificationEvidenceContext) -> None:
        self.context = context

    async def resolve_subject(
        self,
        *,
        task_id: str,
        subject_type: str,
        subject_id: str,
    ) -> VerificationSubject:
        assert task_id == self.context.task_id
        assert subject_type == self.context.subject.subject_type
        assert subject_id == self.context.subject.subject_id
        return self.context.subject

    async def resolve_context(
        self,
        *,
        task_id: str,
        subject_type: str,
        subject_id: str,
    ) -> VerificationEvidenceContext:
        await self.resolve_subject(
            task_id=task_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
        return self.context

    async def validate_evidence_artifacts(
        self,
        *,
        task_id: str,
        artifact_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        assert task_id == self.context.task_id
        return artifact_ids


class _PassExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute_review(self, *, request, agent_run) -> ReviewerExecutionDecision:
        del request, agent_run
        self.calls += 1
        return ReviewerExecutionDecision(outcome=VerificationOutcome.PASS)


class _AlternateMapper:
    adapter_id = "alternate-reviewer-mapper"

    def __init__(self) -> None:
        self.calls = 0

    async def map_agent(self, spec: AgentExecutionSpec) -> OrchestratorMapping:
        self.calls += 1
        return OrchestratorMapping(
            adapter_id=self.adapter_id,
            runtime_ref=f"alternate:{spec.agent_revision.agent_id}:{spec.run_id}",
            metadata={
                "agent_id": spec.agent_revision.agent_id,
                "agent_revision": spec.agent_revision.revision,
                "verification_path": "automatic-review",
            },
        )


def _model(config_id: str, provider_id: str) -> ModelConfiguration:
    return ModelConfiguration(
        config_id=config_id,
        display_name=config_id,
        provider_id=provider_id,
        location=ModelLocation.REMOTE,
        capabilities=ModelCapabilities(
            context_window=32_768,
            streaming=True,
            modalities=("text",),
        ),
        health=HealthStatus.HEALTHY,
        adapter_metadata=(
            AdapterMetadata(namespace="test", values={"model": f"native/{config_id}"}),
        ),
    )


def _profile(name: str, role: str, *, model_id: str | None = None) -> AgentProfile:
    return AgentProfile(
        name=name,
        role=role,
        instructions=AgentInstructions(
            role=InstructionSource(content=f"Act as the exact {role} for this task."),
        ),
        model=(
            AgentModelPolicy(requirements=RoutingRequirements(explicit_model_id=model_id))
            if model_id is not None
            else AgentModelPolicy()
        ),
    )


def _workflow_setup(
    *,
    independence: ReviewerIndependence,
    producer_model_id: str | None = "producer-model",
    producer_provider_id: str | None = "automatic-review-provider-a",
    use_same_agent: bool = False,
):
    registry = ModelRegistry()
    registry.register_provider(_ProviderA())
    registry.register_provider(_ProviderB())
    registry.register_model(_model("producer-model", "automatic-review-provider-a"))
    registry.register_model(_model("reviewer-model", "automatic-review-provider-b"))

    service = AgentService(InMemoryAgentRepository())
    producer = service.create_agent(
        _profile("Producer", "producer", model_id="producer-model"),
        owner_ref=OwnerRef(type="service", id="issue-759"),
    )
    reviewer = (
        producer
        if use_same_agent
        else service.create_agent(
            _profile("Reviewer", "reviewer", model_id="reviewer-model"),
            owner_ref=OwnerRef(type="service", id="issue-759"),
        )
    )
    agents = AgentRuntime(service, model_registry=registry)

    verification = VerificationService()
    policy = verification.register_policy(
        VerificationPolicy(
            name="automatic-reviewer-replaceability",
            stages=(VerificationStage("review", VerifierKind.AGENT),),
            independence=independence,
        )
    )
    task_id = new_id("task")
    run_id = new_id("run")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="1",
        digest="sha256:automatic-reviewer-replaceability",
    )
    producer_identity = ProducerIdentity(
        actor_ref=f"agent:{producer.agent_id}@{producer.revision}",
        agent_id=producer.agent_id,
        agent_revision=producer.revision,
        model_config_id=producer_model_id,
        provider_id=producer_provider_id,
    )
    evidence = _Evidence(
        VerificationEvidenceContext(
            task_id=task_id,
            subject=subject,
            run_id=run_id,
            project_id=None,
            capability_ids=(),
            producer=producer_identity,
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
        run_id=run_id,
        result_id=subject.subject_id,
        producer=producer_identity,
        correlation_id="issue-759-replaceability",
    )
    executor = _PassExecutor()
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
    return workflow, request, executor, agents, producer, reviewer


def test_automatic_workflow_enforces_agent_model_and_provider_independence() -> None:
    async def scenario() -> None:
        workflow, request, executor, _agents, producer, reviewer = _workflow_setup(
            independence=ReviewerIndependence(
                producer_agent_must_differ=True,
                model_must_differ=True,
                provider_must_differ=True,
                agent_reviewer_must_be_read_only=True,
            )
        )

        result = await workflow.run_request(request.verification_id)

        assert result.completion.state is CompletionState.ACCEPTED
        assert result.latest.verification_result is not None
        verifier = result.latest.verification_result.verifier
        assert request.producer is not None
        assert verifier.agent_id == reviewer.agent_id
        assert verifier.agent_id != producer.agent_id
        assert verifier.model_config_id == "reviewer-model"
        assert verifier.model_config_id != request.producer.model_config_id
        assert verifier.provider_id == "automatic-review-provider-b"
        assert verifier.provider_id != request.producer.provider_id
        assert executor.calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("independence", "producer_model", "producer_provider", "same_agent"),
    [
        (
            ReviewerIndependence(producer_agent_must_differ=True),
            "producer-model",
            "automatic-review-provider-a",
            True,
        ),
        (
            ReviewerIndependence(model_must_differ=True),
            "reviewer-model",
            "automatic-review-provider-a",
            False,
        ),
        (
            ReviewerIndependence(provider_must_differ=True),
            "producer-model",
            "automatic-review-provider-b",
            False,
        ),
        (ReviewerIndependence(model_must_differ=True), None, "automatic-review-provider-a", False),
        (ReviewerIndependence(provider_must_differ=True), "producer-model", None, False),
    ],
)
def test_automatic_workflow_fails_closed_when_required_independence_cannot_be_proved(
    independence: ReviewerIndependence,
    producer_model: str | None,
    producer_provider: str | None,
    same_agent: bool,
) -> None:
    async def scenario() -> None:
        workflow, request, executor, agents, _producer, _reviewer = _workflow_setup(
            independence=independence,
            producer_model_id=producer_model,
            producer_provider_id=producer_provider,
            use_same_agent=same_agent,
        )

        with pytest.raises(ContractError) as caught:
            await workflow.run_request(request.verification_id)

        assert caught.value.code is ErrorCode.FORBIDDEN
        assert executor.calls == 0
        assert agents.service.repository.list_agent_runs() == ()

    asyncio.run(scenario())


def test_alternate_orchestrator_mapper_preserves_canonical_automatic_review() -> None:
    async def scenario() -> None:
        workflow, request, executor, _agents, _producer, _reviewer = _workflow_setup(
            independence=ReviewerIndependence(
                producer_agent_must_differ=True,
                model_must_differ=True,
                provider_must_differ=True,
            )
        )
        mapper = _AlternateMapper()

        result = await workflow.run_request(
            request.verification_id,
            options=ReviewerRuntimeOptions(mapper=mapper),
        )

        assert result.completion.state is CompletionState.ACCEPTED
        assert result.latest.verification_result is not None
        assert result.latest.verification_result.outcome is VerificationOutcome.PASS
        assert result.latest.reviewer_run is not None
        assert result.latest.reviewer_run.orchestrator_adapter_id == mapper.adapter_id
        assert result.latest.reviewer_run.orchestrator_runtime_ref is not None
        assert result.latest.reviewer_run.verification_context.get("verification_id") == (
            request.verification_id
        )
        assert mapper.calls == 1
        assert executor.calls == 1

    asyncio.run(scenario())


def test_canonical_automatic_review_modules_have_no_provider_private_dependency() -> None:
    modules = (
        agent_workflow_module,
        reviewer_agent_module,
        output_workflow_module,
        reviewer_routing_module,
        agent_repair_module,
    )
    forbidden = ("ai_multi_agent_platform.adapters.hermes", "forge", "litellm")

    for module in modules:
        source = inspect.getsource(module).casefold()
        for token in forbidden:
            assert token not in source, (
                f"{module.__name__} leaks provider-private dependency {token}"
            )
