from __future__ import annotations

import asyncio

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
from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    ExecutionStatus,
    HealthStatus,
    PlanRequest,
    PlanResponse,
    PlanStepProposal,
    ProviderDescriptor,
)
from ai_multi_agent_platform.domain import OwnerRef, TaskStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    RoutingRequirements,
)
from ai_multi_agent_platform.testing import (
    FakeLifecycleBackend,
    FakeModelProvider,
    FakeOrchestrator,
)
from ai_multi_agent_platform.verification import (
    CompletionState,
    ProducerIdentity,
    ReviewerIndependence,
    VerificationCompletionAuthority,
    VerificationOutcome,
    VerificationPolicy,
    VerificationResult,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)
from ai_multi_agent_platform.verification.reviewer_agent import ReviewerAgentRuntime


class AlternateOrchestrator(FakeOrchestrator):
    async def plan(self, request: PlanRequest) -> PlanResponse:
        self.calls.append(request)
        return PlanResponse(
            summary=f"alternate orchestration for {request.objective}",
            steps=(
                PlanStepProposal(
                    key="alternate-work",
                    title="Alternate work",
                    objective=request.objective,
                    metadata={"orchestrator": "alternate"},
                ),
            ),
        )


class ReviewerProviderA(FakeModelProvider):
    descriptor = ProviderDescriptor(
        provider_id="review-provider-a",
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


class ReviewerProviderB(FakeModelProvider):
    descriptor = ProviderDescriptor(
        provider_id="review-provider-b",
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


def _subject(*, digest: str = "sha256:replacement-conformance") -> VerificationSubject:
    return VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="1",
        digest=digest,
    )


def _human_policy() -> VerificationPolicy:
    return VerificationPolicy(
        name="replacement-human",
        stages=(VerificationStage("human-review", VerifierKind.HUMAN),),
    )


async def _orchestrator_completion_trace(orchestrator: FakeOrchestrator) -> tuple[object, ...]:
    verification = VerificationService()
    completion = VerificationCompletionAuthority(verification)
    policy = verification.register_policy(_human_policy())
    lifecycle = FakeLifecycleBackend()
    kernel = PlatformKernel(
        orchestrator=orchestrator,
        lifecycle=lifecycle,
        repository=InMemoryKernelRepository(),
        completion_authority=completion,
    )
    task = await kernel.create_task(
        idempotency_key="replacement:create",
        title="Replacement conformance",
        objective="Prove Verification is independent from the selected orchestrator",
        owner_type="user",
        owner_id="issue-86-conformance",
    )
    await kernel.ready_task(idempotency_key="replacement:ready", task_id=task.task_id)
    run = await kernel.start_task(idempotency_key="replacement:start", task_id=task.task_id)

    subject = _subject(digest=f"sha256:{orchestrator.__class__.__name__}")
    request = completion.request_verification(
        task_id=task.task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="human-review",
        subject=subject,
        correlation_id=task.task_id,
        run_id=run.run_id,
        result_id=subject.subject_id,
    )
    lifecycle.complete(
        run.run_id,
        status=ExecutionStatus.SUCCEEDED,
        output={"orchestrator": orchestrator.__class__.__name__},
    )
    await kernel.refresh_run(
        idempotency_key="replacement:run-success",
        task_id=task.task_id,
        run_id=run.run_id,
    )
    waiting = await kernel.get_task(task.task_id)
    assert waiting.status is TaskStatus.WAITING
    assert waiting.wait_reason == "verification:waiting"
    assert completion.assess_task_completion(task.task_id).state is CompletionState.WAITING

    verification.record_human_review(
        request.verification_id,
        reviewer_ref="user:replacement-reviewer",
        outcome=VerificationOutcome.PASS,
    )
    assert completion.assess_task_completion(task.task_id).state is CompletionState.ACCEPTED
    completed = await kernel.complete_task(
        idempotency_key="replacement:complete",
        task_id=task.task_id,
    )
    assert completed.status is TaskStatus.SUCCEEDED
    event_types = tuple(event.event_type for event in await kernel.history(task.task_id))
    return waiting.wait_reason, completed.status, event_types[-2:]


def test_orchestrator_replacement_cannot_change_verification_completion_semantics() -> None:
    default_trace = asyncio.run(_orchestrator_completion_trace(FakeOrchestrator()))
    alternate_trace = asyncio.run(_orchestrator_completion_trace(AlternateOrchestrator()))

    assert default_trace == alternate_trace
    assert default_trace == (
        "verification:waiting",
        TaskStatus.SUCCEEDED,
        ("task.resumed", "task.succeeded"),
    )


def _model_configuration(config_id: str, provider_id: str) -> ModelConfiguration:
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


def _reviewer_runtime(
    provider: FakeModelProvider,
    *,
    config_id: str,
) -> tuple[AgentService, ReviewerAgentRuntime, str]:
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(_model_configuration(config_id, provider.descriptor.provider_id))
    service = AgentService(InMemoryAgentRepository())
    agent = service.create_agent(
        AgentProfile(
            name=f"Reviewer {provider.descriptor.provider_id}",
            role="reviewer",
            instructions=AgentInstructions(
                role=InstructionSource(content="Review the exact bound result."),
            ),
            model=AgentModelPolicy(
                requirements=RoutingRequirements(explicit_model_id=config_id),
            ),
        ),
        owner_ref=OwnerRef(type="service", id="verification"),
    )
    return (
        service,
        ReviewerAgentRuntime(VerificationService(), AgentRuntime(service, model_registry=registry)),
        agent.agent_id,
    )


async def _agent_reviewer_outcome(
    provider: FakeModelProvider,
    *,
    config_id: str,
) -> tuple[CompletionState, str | None, str | None]:
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(_model_configuration(config_id, provider.descriptor.provider_id))
    agent_service = AgentService(InMemoryAgentRepository())
    agent = agent_service.create_agent(
        AgentProfile(
            name=f"Reviewer {provider.descriptor.provider_id}",
            role="reviewer",
            instructions=AgentInstructions(
                role=InstructionSource(content="Review the exact bound result."),
            ),
            model=AgentModelPolicy(
                requirements=RoutingRequirements(explicit_model_id=config_id),
            ),
        ),
        owner_ref=OwnerRef(type="service", id="verification"),
    )
    verification = VerificationService()
    policy = verification.register_policy(
        VerificationPolicy(
            name="replaceable-reviewer-model",
            stages=(VerificationStage("review", VerifierKind.AGENT),),
        )
    )
    subject = _subject(digest=f"sha256:{config_id}")
    request = verification.request_verification(
        task_id=new_id("task"),
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        result_id=subject.subject_id,
        correlation_id=f"replacement:{config_id}",
    )
    reviewer = ReviewerAgentRuntime(
        verification,
        AgentRuntime(agent_service, model_registry=registry),
    )
    record = await reviewer.start_review(
        request.verification_id,
        run_id=new_id("run"),
        agent_id=agent.agent_id,
    )
    result = reviewer.complete_review(
        record.agent_run_id,
        outcome=VerificationOutcome.PASS,
    )
    decision = verification.assess_completion(
        task_id=request.task_id,
        subject=subject,
        policy_id=policy.policy_id,
        policy_version=policy.version,
    )
    return decision.state, result.verifier.model_config_id, result.verifier.provider_id


@pytest.mark.parametrize(
    ("provider", "config_id"),
    [
        (ReviewerProviderA(), "review-model-a"),
        (ReviewerProviderB(), "review-model-b"),
    ],
)
def test_reviewer_model_and_provider_replacement_preserves_acceptance(
    provider: FakeModelProvider,
    config_id: str,
) -> None:
    state, selected_model, selected_provider = asyncio.run(
        _agent_reviewer_outcome(provider, config_id=config_id)
    )
    assert state is CompletionState.ACCEPTED
    assert selected_model == config_id
    assert selected_provider == provider.descriptor.provider_id


def test_reviewer_provider_independence_uses_actual_routed_provider_identity() -> None:
    async def scenario() -> None:
        registry = ModelRegistry()
        registry.register_provider(ReviewerProviderA())
        registry.register_provider(ReviewerProviderB())
        registry.register_model(_model_configuration("review-model-a", "review-provider-a"))
        registry.register_model(_model_configuration("review-model-b", "review-provider-b"))
        agent_service = AgentService(InMemoryAgentRepository())
        agent = agent_service.create_agent(
            AgentProfile(
                name="Replaceable reviewer",
                role="reviewer",
                instructions=AgentInstructions(
                    role=InstructionSource(content="Review independently."),
                ),
                model=AgentModelPolicy(
                    requirements=RoutingRequirements(explicit_model_id="review-model-a"),
                    allow_task_override=True,
                ),
            ),
            owner_ref=OwnerRef(type="service", id="verification"),
        )
        verification = VerificationService()
        policy = verification.register_policy(
            VerificationPolicy(
                name="provider-independent-review",
                stages=(VerificationStage("review", VerifierKind.AGENT),),
                independence=ReviewerIndependence(provider_must_differ=True),
            )
        )
        subject = _subject()
        first = verification.request_verification(
            task_id=new_id("task"),
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=subject,
            result_id=subject.subject_id,
            correlation_id="same-provider",
            producer=ProducerIdentity(
                actor_ref="agent:producer",
                provider_id="review-provider-a",
            ),
        )
        reviewer = ReviewerAgentRuntime(
            verification,
            AgentRuntime(agent_service, model_registry=registry),
        )
        with pytest.raises(ContractError) as same_provider:
            await reviewer.start_review(
                first.verification_id,
                run_id=new_id("run"),
                agent_id=agent.agent_id,
            )
        assert same_provider.value.code is ErrorCode.FORBIDDEN
        assert agent_service.repository.list_agent_runs() == ()

        second_subject = _subject(digest="sha256:alternate-provider")
        second = verification.request_verification(
            task_id=new_id("task"),
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=second_subject,
            result_id=second_subject.subject_id,
            correlation_id="alternate-provider",
            producer=ProducerIdentity(
                actor_ref="agent:producer",
                provider_id="review-provider-a",
            ),
        )
        record = await reviewer.start_review(
            second.verification_id,
            run_id=new_id("run"),
            agent_id=agent.agent_id,
            task_model_override=RoutingRequirements(explicit_model_id="review-model-b"),
        )
        result = reviewer.complete_review(
            record.agent_run_id,
            outcome=VerificationOutcome.PASS,
        )
        assert result.verifier.provider_id == "review-provider-b"
        assert result.verifier.model_config_id == "review-model-b"

    asyncio.run(scenario())


@pytest.mark.parametrize("provider_id", ["domain-verifier-a", "domain-verifier-b"])
def test_external_verification_provider_replacement_uses_same_exact_subject_policy(
    provider_id: str,
) -> None:
    verification = VerificationService()
    policy = verification.register_policy(
        VerificationPolicy(
            name="external-provider-conformance",
            stages=(VerificationStage("domain", VerifierKind.PROVIDER),),
        )
    )
    task_id = new_id("task")
    subject = _subject(digest=f"sha256:{provider_id}")
    request = verification.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="domain",
        subject=subject,
        result_id=subject.subject_id,
        correlation_id=f"provider:{provider_id}",
    )
    verification.submit_result(
        VerificationResult(
            verification_id=request.verification_id,
            verifier=VerifierIdentity(
                verifier_ref=f"provider:{provider_id}",
                kind=VerifierKind.PROVIDER,
                provider_id=provider_id,
                read_only=True,
            ),
            outcome=VerificationOutcome.PASS,
            subject=subject,
            checks_executed=("external_domain_review",),
        )
    )
    decision = verification.assess_completion(
        task_id=task_id,
        subject=subject,
        policy_id=policy.policy_id,
        policy_version=policy.version,
    )
    assert decision.state is CompletionState.ACCEPTED
    result = verification.result_for(request.verification_id)
    assert result is not None
    assert result.verifier.provider_id == provider_id
