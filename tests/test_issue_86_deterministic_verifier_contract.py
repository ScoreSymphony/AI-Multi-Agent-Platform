"""Conformance coverage for the provider-neutral deterministic verifier contract (#86)."""

from __future__ import annotations

import asyncio

from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    DeterministicVerifier,
    VerificationCompletionAuthority,
    VerificationEvidenceContext,
    VerificationOutcome,
    VerificationPolicy,
    VerificationRequest,
    VerificationResult,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)


class _IndependentDeterministicVerifier:
    """Independent implementation that does not inherit from the reference verifier."""

    def verify(self, request: VerificationRequest) -> VerificationResult:
        return VerificationResult(
            verification_id=request.verification_id,
            verifier=VerifierIdentity(
                verifier_ref="deterministic:independent",
                kind=VerifierKind.DETERMINISTIC,
                read_only=True,
            ),
            outcome=VerificationOutcome.PASS,
            subject=request.subject,
            checks_executed=("independent_contract",),
        )


class _EvidenceResolver:
    def __init__(self, subject: VerificationSubject) -> None:
        self.subject = subject

    async def resolve_subject(
        self,
        *,
        task_id: str,
        subject_type: str,
        subject_id: str,
    ) -> VerificationSubject:
        del task_id, subject_type, subject_id
        return self.subject

    async def resolve_context(
        self,
        *,
        task_id: str,
        subject_type: str,
        subject_id: str,
    ) -> VerificationEvidenceContext:
        del subject_type, subject_id
        return VerificationEvidenceContext(
            task_id=task_id,
            subject=self.subject,
            run_id=None,
            project_id=None,
            capability_ids=(),
            producer=None,
        )

    async def validate_evidence_artifacts(
        self,
        *,
        task_id: str,
        artifact_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        del task_id
        return artifact_ids


def _subject() -> VerificationSubject:
    return VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="1",
        digest="sha256:deterministic-contract",
    )


def _policy(service: VerificationService) -> VerificationPolicy:
    return service.register_policy(
        VerificationPolicy(
            name="deterministic-verifier-contract",
            stages=(VerificationStage("checks", VerifierKind.DETERMINISTIC),),
        )
    )


def test_service_accepts_independent_deterministic_verifier() -> None:
    service = VerificationService()
    subject = _subject()
    policy = _policy(service)
    request = service.request_verification(
        task_id=new_id("task"),
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="checks",
        subject=subject,
        correlation_id="deterministic-contract-service",
    )
    verifier = _IndependentDeterministicVerifier()

    assert isinstance(verifier, DeterministicVerifier)
    result = service.run_deterministic(request.verification_id, verifier)

    assert result.outcome is VerificationOutcome.PASS
    assert result.checks_executed == ("independent_contract",)
    assert service.result_for(request.verification_id) == result


def test_canonical_runtime_accepts_independent_deterministic_verifier() -> None:
    async def scenario() -> None:
        subject = _subject()
        evidence = _EvidenceResolver(subject)
        service = VerificationService(
            require_canonical_subjects=True,
            require_canonical_results=True,
        )
        completion = VerificationCompletionAuthority(service)
        runtime = CanonicalVerificationRuntime(completion, evidence)
        policy = _policy(service)
        request = await runtime.request_verification(
            task_id=new_id("task"),
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="checks",
            subject_type="result",
            subject_id=subject.subject_id,
            correlation_id="deterministic-contract-runtime",
        )
        verifier = _IndependentDeterministicVerifier()

        result = await runtime.run_deterministic(request.verification_id, verifier)

        assert result.outcome is VerificationOutcome.PASS
        assert result.checks_executed == ("independent_contract",)
        assert service.result_for(request.verification_id) == result

    asyncio.run(scenario())
