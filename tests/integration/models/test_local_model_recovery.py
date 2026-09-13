from __future__ import annotations

import asyncio

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import HealthStatus
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
    VerificationPolicy,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)
from ai_multi_agent_platform.verification.agent_workflow import (
    AutomaticReviewerWorkflow,
    ConfiguredReviewerResolver,
    ReviewerAssignment,
)
from ai_multi_agent_platform.verification.persistence import SqliteVerificationService
from ai_multi_agent_platform.verification.reference_reviewer import (
    ModelRuntimeReviewerExecutor,
    ReviewerSubjectInput,
)
from ai_multi_agent_platform.verification.reviewer_recovery import (
    AutomaticReviewerStartupReconciler,
    ReviewerRecoveryDisposition,
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


def test_startup_recovery_runs_through_local_model_provider_without_paid_service(tmp_path) -> None:
    async def scenario() -> None:
        model_provider = FakeModelProvider(
            response_text='{"outcome":"pass","findings":[]}',
            model_ref="native-local-restart-reviewer",
        )
        model_registry = ModelRegistry()
        model_registry.register_provider(model_provider)
        model_registry.register_model(
            ModelConfiguration(
                config_id="local-restart-review-model",
                display_name="Local restart review model",
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
            owner_ref=OwnerRef(type="service", id="issue-758-local"),
        )
        reviewer = service.create_agent(
            _profile("Reviewer", "reviewer"),
            owner_ref=OwnerRef(type="service", id="issue-758-local"),
        )
        agents = AgentRuntime(service, model_registry=model_registry)

        verification = SqliteVerificationService(tmp_path / "verification-local-recovery.sqlite3")
        policy = verification.register_policy(
            VerificationPolicy(
                name="local automatic restart review",
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
            digest="sha256:issue-758-local-restart-subject",
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
            correlation_id="issue-758-local-restart-review",
        )
        inputs = ExactInputProvider(
            ReviewerSubjectInput(
                subject=subject,
                content="The locally produced answer is ready for restart-safe review.",
            )
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
            executor=ModelRuntimeReviewerExecutor(
                agents=agents,
                models=models,
                inputs=inputs,
            ),
        )
        recovery = AutomaticReviewerStartupReconciler(
            workflow=workflow,
            agents=agents,
            verification=verification,
        )

        first = await recovery.reconcile_startup()
        repeated = await recovery.reconcile_startup()

        assert first[0].disposition is ReviewerRecoveryDisposition.DISPATCHED
        assert repeated[0].disposition is ReviewerRecoveryDisposition.ALREADY_COMPLETED
        assert completion.assess_task_completion(request.task_id).state is CompletionState.ACCEPTED
        assert inputs.calls == 1
        assert len(model_provider.calls) == 1
        reviewer_runs = agents.service.repository.list_agent_runs()
        assert len(reviewer_runs) == 1
        assert reviewer_runs[0].selected_model_config_id == "local-restart-review-model"
        assert reviewer_runs[0].selected_provider_id == model_provider.descriptor.provider_id

    asyncio.run(scenario())
