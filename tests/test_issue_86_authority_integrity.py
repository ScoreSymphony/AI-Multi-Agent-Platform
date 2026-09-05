from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentRunRecord,
    AgentRunStatus,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
    new_agent_run_id,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, ExecutionStatus
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    KernelFileVerificationEvidenceResolver,
    ReviewerIndependence,
    VerificationCompletionAuthority,
    VerificationOutcome,
    VerificationPolicy,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)


def test_strict_canonical_mode_rejects_raw_subject_requests(tmp_path) -> None:
    service = VerificationService(require_canonical_subjects=True)
    completion = VerificationCompletionAuthority(service)
    policy = service.register_policy(
        VerificationPolicy(
            name="strict canonical",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
        )
    )
    task_id = new_id("task")
    result_id = new_id("result")
    forged = VerificationSubject(
        subject_type="result",
        subject_id=result_id,
        revision="forged",
        digest="sha256:forged",
    )
    with pytest.raises(ContractError) as error:
        completion.request_verification(
            task_id=task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=forged,
            correlation_id=task_id,
            result_id=result_id,
        )
    assert error.value.code is ErrorCode.FORBIDDEN


def test_canonical_runtime_derives_producer_project_and_capabilities(tmp_path) -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=repository,
        )
        project_id = new_id("project")
        task = await kernel.create_task(
            idempotency_key="producer:create",
            title="Canonical producer",
            objective="Derive producer provenance",
            owner_type="user",
            owner_id="issue-86",
            project_id=project_id,
        )
        await kernel.ready_task(idempotency_key="producer:ready", task_id=task.task_id)
        run = await kernel.start_task(idempotency_key="producer:start", task_id=task.task_id)
        lifecycle.complete(run.run_id, status=ExecutionStatus.SUCCEEDED, output={"answer": 86})
        await kernel.refresh_run(
            idempotency_key="producer:refresh", task_id=task.task_id, run_id=run.run_id
        )
        result_id = new_id("result")
        await kernel.attach_result(
            idempotency_key="producer:result",
            task_id=task.task_id,
            run_id=run.run_id,
            result_id=result_id,
        )
        agent_id = new_id("agent")
        capability_id = new_id("cap")
        agents = InMemoryAgentRepository()
        producer_revision = AgentService(agents).create_agent(
            AgentProfile(
                name="Canonical producer",
                role="producer",
                instructions=AgentInstructions(
                    role=InstructionSource(content="Produce the exact canonical result.")
                ),
            ),
            owner_ref=OwnerRef(type="service", id="issue-86"),
            agent_id=agent_id,
        )
        agents.create_agent_run(
            AgentRunRecord(
                agent_run_id=new_agent_run_id(),
                run_id=run.run_id,
                task_id=task.task_id,
                agent=AgentRevisionRef(
                    agent_id=agent_id,
                    revision=producer_revision.revision,
                ),
                status=AgentRunStatus.SUCCEEDED,
                selected_model_config_id="producer-model",
                selected_provider_id="producer-provider",
                capability_ids=(capability_id,),
                capability_versions={capability_id: "1"},
                result_ids=(result_id,),
            )
        )
        service = VerificationService(require_canonical_subjects=True)
        completion = VerificationCompletionAuthority(service)
        policy = service.register_policy(
            VerificationPolicy(
                name="derived scope",
                stages=(VerificationStage("review", VerifierKind.HUMAN),),
                independence=ReviewerIndependence(human_reviewer_must_differ=True),
            )
        )
        resolver = KernelFileVerificationEvidenceResolver(
            kernel,
            repository,
            LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3"),
            agents,
        )
        runtime = CanonicalVerificationRuntime(completion, resolver)
        request = await runtime.request_verification(
            task_id=task.task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject_type="result",
            subject_id=result_id,
            correlation_id=task.task_id,
        )
        assert request.project_id == project_id
        assert request.capability_ids == (capability_id,)
        assert request.run_id == run.run_id
        assert request.producer is not None
        assert request.producer.agent_id == agent_id
        assert request.producer.agent_revision == producer_revision.revision
        assert request.producer.model_config_id == "producer-model"
        assert request.producer.provider_id == "producer-provider"

    asyncio.run(scenario())


def test_reverification_derives_task_and_replaces_producer_context(tmp_path) -> None:
    class FakeEvidence:
        def __init__(self, task_id: str, first: VerificationSubject, second: VerificationSubject):
            self.task_id = task_id
            self.first = first
            self.second = second
            self.use_second = False

        async def resolve_subject(self, *, task_id: str, subject_type: str, subject_id: str):
            assert task_id == self.task_id
            return self.second if self.use_second else self.first

        async def resolve_context(self, *, task_id: str, subject_type: str, subject_id: str):
            from ai_multi_agent_platform.verification import (
                ProducerIdentity,
                VerificationEvidenceContext,
            )

            assert task_id == self.task_id
            subject = self.second if self.use_second else self.first
            suffix = "b" if self.use_second else "a"
            return VerificationEvidenceContext(
                task_id=task_id,
                subject=subject,
                run_id=new_id("run"),
                project_id=None,
                capability_ids=(),
                producer=ProducerIdentity(
                    actor_ref=f"agent:producer-{suffix}",
                    agent_id=new_id("agent"),
                    agent_revision=1,
                    model_config_id=f"model-{suffix}",
                    provider_id=f"provider-{suffix}",
                ),
            )

        async def validate_evidence_artifacts(self, *, task_id: str, artifact_ids: tuple[str, ...]):
            return artifact_ids

    task_id = new_id("task")
    first_result = new_id("result")
    second_result = new_id("result")
    first = VerificationSubject("result", first_result, "1", "sha256:first")
    second = VerificationSubject("result", second_result, "2", "sha256:second")
    service = VerificationService(require_canonical_subjects=True)
    completion = VerificationCompletionAuthority(service)
    policy = service.register_policy(
        VerificationPolicy(
            name="repair producer replacement",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            max_repair_attempts=1,
        )
    )
    evidence = FakeEvidence(task_id, first, second)
    runtime = CanonicalVerificationRuntime(completion, evidence)

    async def scenario() -> None:
        initial = await runtime.request_verification(
            task_id=task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject_type="result",
            subject_id=first_result,
            correlation_id=task_id,
        )
        service.record_human_review(
            initial.verification_id,
            reviewer_ref="user:reviewer",
            outcome=VerificationOutcome.NEEDS_CHANGES,
        )
        first_producer = initial.producer
        assert first_producer is not None
        evidence.use_second = True
        repaired = await runtime.request_reverification_after_repair(
            initial.verification_id,
            subject_type="result",
            subject_id=second_result,
            correlation_id=task_id,
        )
        assert repaired.task_id == task_id
        assert repaired.producer is not None
        assert repaired.producer.model_config_id == "model-b"
        assert repaired.producer.provider_id == "provider-b"
        assert repaired.producer.actor_ref != first_producer.actor_ref

    asyncio.run(scenario())
