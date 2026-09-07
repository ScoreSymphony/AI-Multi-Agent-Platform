"""Canonical #86 Verification binding for Research Items, Claims and Evidence."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import validate_id
from ai_multi_agent_platform.verification import CanonicalVerificationAccess
from ai_multi_agent_platform.verification.models import (
    ProducerIdentity,
    VerificationOutcome,
    VerificationRequest,
    VerificationResult,
    VerificationSubject,
)
from ai_multi_agent_platform.verification.service import VerificationService

from .models import ResearchVerificationBinding, ResearchVerificationSubjectType
from .service import ResearchService


@dataclass(frozen=True, slots=True)
class ResearchVerificationSubject(VerificationSubject):
    """Exact Research revision/digest reviewed by the existing #86 authority."""

    def __post_init__(self) -> None:
        try:
            subject_type = ResearchVerificationSubjectType(self.subject_type)
        except ValueError as exc:
            raise ValueError("invalid Research verification subject_type") from exc
        validate_id(self.subject_id, subject_type.value)
        if not self.revision.strip():
            raise ValueError("Research verification revision must not be blank")
        if not self.digest.strip():
            raise ValueError("Research verification digest must not be blank")


class ResearchVerificationBridge:
    """Use #86 without implicitly making Research review a Task-completion gate.

    #86 remains the authority for policy, reviewer independence and result acceptance. This bridge
    resolves exact Research subjects and records PASS bindings for downstream provenance. Task
    completion remains owned by VerificationCompletionAuthority separately.
    """

    def __init__(self, research: ResearchService, verification: VerificationService) -> None:
        self.research = research
        self.verification = verification
        self._canonical = CanonicalVerificationAccess(verification)

    def resolve_subject(
        self,
        subject_type: ResearchVerificationSubjectType,
        subject_id: str,
    ) -> ResearchVerificationSubject:
        if subject_type is ResearchVerificationSubjectType.ITEM:
            item = self.research.repository.get_item(subject_id)
            return ResearchVerificationSubject(
                subject_type=subject_type.value,
                subject_id=item.research_item_id,
                revision=str(item.revision),
                digest=item.digest,
            )
        if subject_type is ResearchVerificationSubjectType.CLAIM:
            claim = self.research.repository.get_claim(subject_id)
            return ResearchVerificationSubject(
                subject_type=subject_type.value,
                subject_id=claim.claim_id,
                revision=str(claim.revision),
                digest=claim.digest,
            )
        evidence = self.research.repository.get_evidence(subject_id)
        return ResearchVerificationSubject(
            subject_type=subject_type.value,
            subject_id=evidence.evidence_id,
            revision=evidence.source_observation_id,
            digest=evidence.digest,
        )

    def request_verification(
        self,
        *,
        subject_type: ResearchVerificationSubjectType,
        subject_id: str,
        policy_id: str,
        policy_version: int,
        stage_id: str,
        correlation_id: str,
        task_id: str | None = None,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        subject = self.resolve_subject(subject_type, subject_id)
        item_id, producer, artifact_ids = self._context(subject_type, subject_id)
        item = self.research.repository.get_item(item_id)
        resolved_task_id = task_id or item.task_id
        if resolved_task_id is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "#86 Research verification requires a canonical Task context",
            )
        if item.task_id is not None and item.task_id != resolved_task_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Research verification Task differs from Research Item provenance",
            )
        return self._canonical.request_verification(
            task_id=resolved_task_id,
            policy_id=policy_id,
            policy_version=policy_version,
            stage_id=stage_id,
            subject=subject,
            correlation_id=correlation_id,
            artifact_ids=artifact_ids,
            project_id=item.project_id,
            producer=producer,
            causation_id=causation_id,
        )

    def submit_result(self, result: VerificationResult) -> VerificationResult:
        request = self.verification.get_request(result.verification_id)
        subject_type = self._research_subject_type(request.subject.subject_type)
        current = self.resolve_subject(subject_type, request.subject.subject_id)
        if current != request.subject or result.subject != current:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Research subject changed after Verification was requested",
            )
        stored = self._canonical.submit_result(result)
        if stored.outcome is VerificationOutcome.PASS:
            self._record_pass_binding(request, current)
        return stored

    def bind_completed_pass(self, verification_id: str) -> ResearchVerificationBinding:
        """Persist a PASS produced through another existing #86 review entrypoint."""

        request = self.verification.get_request(verification_id)
        result = self.verification.result_for(verification_id)
        if result is None or result.outcome is not VerificationOutcome.PASS:
            raise ContractError(
                ErrorCode.CONFLICT,
                "only completed PASS Verification can become Research action provenance",
            )
        subject_type = self._research_subject_type(request.subject.subject_type)
        current = self.resolve_subject(subject_type, request.subject.subject_id)
        if current != request.subject or result.subject != current:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "completed Verification no longer matches current Research revision",
            )
        return self._record_pass_binding(request, current)

    def _record_pass_binding(
        self,
        request: VerificationRequest,
        subject: ResearchVerificationSubject,
    ) -> ResearchVerificationBinding:
        subject_type = self._research_subject_type(subject.subject_type)
        item_id, _, _ = self._context(subject_type, subject.subject_id)
        binding = ResearchVerificationBinding(
            research_item_id=item_id,
            verification_id=request.verification_id,
            subject_type=subject_type,
            subject_id=subject.subject_id,
            subject_revision=subject.revision,
            subject_digest=subject.digest,
        )
        stored = self.research.repository.create_verification_binding(binding)
        self.research.record_verification_id(item_id, stored.verification_id)
        return stored

    def _context(
        self,
        subject_type: ResearchVerificationSubjectType,
        subject_id: str,
    ) -> tuple[str, ProducerIdentity | None, tuple[str, ...]]:
        if subject_type is ResearchVerificationSubjectType.ITEM:
            item = self.research.repository.get_item(subject_id)
            return item.research_item_id, None, ()
        if subject_type is ResearchVerificationSubjectType.CLAIM:
            claim = self.research.repository.get_claim(subject_id)
            producer = None
            if claim.author_ref is not None or claim.agent_id is not None:
                producer = ProducerIdentity(
                    actor_ref=claim.author_ref or f"agent:{claim.agent_id}",
                    agent_id=claim.agent_id,
                    agent_revision=claim.agent_revision,
                )
            return claim.research_item_id, producer, ()
        evidence = self.research.repository.get_evidence(subject_id)
        producer = None
        if evidence.agent_id is not None:
            producer = ProducerIdentity(
                actor_ref=f"agent:{evidence.agent_id}",
                agent_id=evidence.agent_id,
                agent_revision=evidence.agent_revision,
            )
        artifact_ids = () if evidence.artifact_id is None else (evidence.artifact_id,)
        return evidence.research_item_id, producer, artifact_ids

    @staticmethod
    def _research_subject_type(value: str) -> ResearchVerificationSubjectType:
        try:
            return ResearchVerificationSubjectType(value)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Verification request is not bound to a Research subject",
            ) from exc
