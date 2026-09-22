"""Async-safe canonical Verification runtime backed by the persistence offload boundary."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .async_persistence import (
    AsyncVerificationCompletionAuthority,
    AsyncVerificationService,
    runtime_verification_completion,
    runtime_verification_service,
)
from .deterministic import DeterministicVerifier
from .evidence import (
    CanonicalVerificationRuntime,
    VerificationEvidenceContext,
    VerificationEvidenceResolver,
)
from .gate import TaskVerificationRequirement, VerificationCompletionAuthority
from .models import VerificationRequest, VerificationResult, VerifierKind


class AsyncCanonicalVerificationRuntime(CanonicalVerificationRuntime):
    """Canonical evidence runtime whose persistence-bearing operations are awaitable.

    The synchronous parent remains the setup/offline compatibility seam. This runtime is the
    production-facing composition for asyncio callers and delegates all Verification state access
    that can reach persistence through the shared bounded offload boundary.
    """

    def __init__(
        self,
        completion: VerificationCompletionAuthority,
        evidence: VerificationEvidenceResolver,
        *,
        runtime_verification: AsyncVerificationService | None = None,
        runtime_completion: AsyncVerificationCompletionAuthority | None = None,
    ) -> None:
        super().__init__(completion, evidence)
        self._runtime_verification = runtime_verification_service(
            completion.verification,
            runtime_service=runtime_verification,
        )
        self._runtime_completion = runtime_verification_completion(
            completion,
            runtime_completion=runtime_completion,
        )

    @property
    def runtime_verification(self) -> AsyncVerificationService:
        return self._runtime_verification

    @property
    def runtime_completion(self) -> AsyncVerificationCompletionAuthority:
        return self._runtime_completion

    async def async_require_task(
        self,
        *,
        task_id: str,
        policy_id: str,
        policy_version: int,
    ) -> TaskVerificationRequirement:
        """Awaitable counterpart to the synchronous setup compatibility seam."""

        return await self._runtime_completion.require_task(
            task_id=task_id,
            policy_id=policy_id,
            policy_version=policy_version,
        )

    async def request_verification(
        self,
        *,
        task_id: str,
        policy_id: str,
        policy_version: int,
        stage_id: str,
        subject_type: str,
        subject_id: str,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        context = await self._evidence.resolve_context(
            task_id=task_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
        result_id = subject_id if subject_type == "result" else None
        artifact_ids = (subject_id,) if subject_type == "artifact" else ()
        return await self._runtime_completion.request_canonical_verification(
            task_id=context.task_id,
            policy_id=policy_id,
            policy_version=policy_version,
            stage_id=stage_id,
            subject=context.subject,
            correlation_id=correlation_id,
            run_id=context.run_id,
            result_id=result_id,
            artifact_ids=artifact_ids,
            project_id=context.project_id,
            capability_ids=context.capability_ids,
            producer=context.producer,
            causation_id=causation_id,
        )

    async def submit_result(self, result: VerificationResult) -> VerificationResult:
        request = await self._runtime_verification.get_request(result.verification_id)
        canonical_subject = await self._evidence.resolve_subject(
            task_id=request.task_id,
            subject_type=request.subject.subject_type,
            subject_id=request.subject.subject_id,
        )
        if canonical_subject != request.subject or result.subject != canonical_subject:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "verification result subject differs from current canonical evidence",
            )
        canonical_result = await self._with_canonical_evidence_bindings(
            task_id=request.task_id,
            result=result,
        )
        return await self._runtime_verification.submit_canonical_result(canonical_result)

    async def run_deterministic(
        self,
        verification_id: str,
        verifier: DeterministicVerifier,
    ) -> VerificationResult:
        request = await self._runtime_verification.get_request(verification_id)
        if request.requested_verifier_kind is not VerifierKind.DETERMINISTIC:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "verification request does not require deterministic verification",
            )
        return await self.submit_result(verifier.verify(request))

    async def request_reverification_after_repair(
        self,
        verification_id: str,
        *,
        subject_type: str,
        subject_id: str,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        previous = await self._runtime_verification.get_request(verification_id)
        context = await self._evidence.resolve_context(
            task_id=previous.task_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
        result_id = subject_id if subject_type == "result" else None
        artifact_ids = (subject_id,) if subject_type == "artifact" else ()
        existing = await self._existing_runtime_reverification_after_repair(
            previous=previous,
            context=context,
            result_id=result_id,
            artifact_ids=artifact_ids,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        if existing is not None:
            return existing
        return await self._runtime_completion.request_canonical_reverification_after_repair(
            verification_id,
            new_subject=context.subject,
            correlation_id=correlation_id,
            run_id=context.run_id,
            result_id=result_id,
            artifact_ids=artifact_ids,
            project_id=context.project_id,
            capability_ids=context.capability_ids,
            producer=context.producer,
            causation_id=causation_id,
        )

    async def _existing_runtime_reverification_after_repair(
        self,
        *,
        previous: VerificationRequest,
        context: VerificationEvidenceContext,
        result_id: str | None,
        artifact_ids: tuple[str, ...],
        correlation_id: str,
        causation_id: str | None,
    ) -> VerificationRequest | None:
        next_attempt = previous.repair_attempt + 1
        history = await self._runtime_verification.history(task_id=previous.task_id)
        candidates = tuple(
            request
            for request, _result in history
            if request.verification_id != previous.verification_id
            and request.policy_id == previous.policy_id
            and request.policy_version == previous.policy_version
            and request.stage_id == previous.stage_id
            and request.repair_attempt == next_attempt
        )
        exact = tuple(
            request
            for request in candidates
            if request.requested_verifier_kind is previous.requested_verifier_kind
            and request.subject == context.subject
            and request.run_id == context.run_id
            and request.result_id == result_id
            and request.artifact_ids == artifact_ids
            and request.project_id == context.project_id
            and request.capability_ids == context.capability_ids
            and request.producer == context.producer
            and request.correlation_id == correlation_id
            and request.causation_id == causation_id
        )
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repair output maps to multiple canonical reverification requests",
                details={
                    "source_verification_id": previous.verification_id,
                    "verification_ids": [request.verification_id for request in exact],
                },
            )

        lineage_conflicts = tuple(
            request
            for request in candidates
            if request.run_id == context.run_id
            or (causation_id is not None and request.causation_id == causation_id)
        )
        if lineage_conflicts:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "persisted repair reverification conflicts with current canonical evidence",
                details={
                    "source_verification_id": previous.verification_id,
                    "verification_ids": [request.verification_id for request in lineage_conflicts],
                    "repair_attempt": next_attempt,
                    "subject_id": context.subject.subject_id,
                    "run_id": context.run_id,
                },
            )
        return None


__all__ = ["AsyncCanonicalVerificationRuntime"]
