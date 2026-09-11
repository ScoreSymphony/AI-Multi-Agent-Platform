from __future__ import annotations

import asyncio

from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    ProducerIdentity,
    VerificationCompletionAuthority,
    VerificationEvidenceContext,
    VerificationOutcome,
    VerificationPolicy,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)


class _Evidence:
    def __init__(self, contexts: dict[str, VerificationEvidenceContext]) -> None:
        self._contexts = contexts

    async def resolve_subject(self, *, task_id: str, subject_type: str, subject_id: str):
        context = self._contexts[subject_id]
        assert context.task_id == task_id
        assert context.subject.subject_type == subject_type
        return context.subject

    async def resolve_context(self, *, task_id: str, subject_type: str, subject_id: str):
        context = self._contexts[subject_id]
        assert context.task_id == task_id
        assert context.subject.subject_type == subject_type
        return context

    async def validate_evidence_artifacts(self, *, task_id: str, artifact_ids: tuple[str, ...]):
        del task_id
        return artifact_ids


def test_repair_reverification_reuses_persisted_exact_child_after_retry() -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        project_id = new_id("project")
        first_result_id = new_id("result")
        repaired_result_id = new_id("result")
        first_run_id = new_id("run")
        repair_run_id = new_id("run")
        first_subject = VerificationSubject(
            "result", first_result_id, f"{first_run_id}:attempt:1", "sha256:first"
        )
        repaired_subject = VerificationSubject(
            "result", repaired_result_id, f"{repair_run_id}:attempt:1", "sha256:repaired"
        )
        first_producer = ProducerIdentity(
            actor_ref="agent:producer-a",
            agent_id=new_id("agent"),
            agent_revision=1,
            model_config_id="model-a",
            provider_id="provider-a",
        )
        repaired_producer = ProducerIdentity(
            actor_ref="agent:producer-b",
            agent_id=new_id("agent"),
            agent_revision=1,
            model_config_id="model-b",
            provider_id="provider-b",
        )
        contexts = {
            first_result_id: VerificationEvidenceContext(
                task_id=task_id,
                subject=first_subject,
                run_id=first_run_id,
                project_id=project_id,
                capability_ids=("cap.read",),
                producer=first_producer,
            ),
            repaired_result_id: VerificationEvidenceContext(
                task_id=task_id,
                subject=repaired_subject,
                run_id=repair_run_id,
                project_id=project_id,
                capability_ids=("cap.read",),
                producer=repaired_producer,
            ),
        }
        service = VerificationService(require_canonical_subjects=True)
        completion = VerificationCompletionAuthority(service)
        policy = service.register_policy(
            VerificationPolicy(
                name="repair replay recovery",
                stages=(VerificationStage("review", VerifierKind.HUMAN),),
                max_repair_attempts=1,
            )
        )
        runtime = CanonicalVerificationRuntime(completion, _Evidence(contexts))
        initial = await runtime.request_verification(
            task_id=task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject_type="result",
            subject_id=first_result_id,
            correlation_id=task_id,
        )
        service.record_human_review(
            initial.verification_id,
            reviewer_ref="user:reviewer",
            outcome=VerificationOutcome.NEEDS_CHANGES,
        )

        first_child = await runtime.request_reverification_after_repair(
            initial.verification_id,
            subject_type="result",
            subject_id=repaired_result_id,
            correlation_id=task_id,
            causation_id=repair_run_id,
        )
        replayed_child = await runtime.request_reverification_after_repair(
            initial.verification_id,
            subject_type="result",
            subject_id=repaired_result_id,
            correlation_id=task_id,
            causation_id=repair_run_id,
        )

        assert replayed_child.verification_id == first_child.verification_id
        assert replayed_child.subject == repaired_subject
        assert replayed_child.run_id == repair_run_id
        assert replayed_child.producer == repaired_producer
        assert replayed_child.repair_attempt == 1
        assert len(service.history(task_id=task_id)) == 2

    asyncio.run(scenario())
