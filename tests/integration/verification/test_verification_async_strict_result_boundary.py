from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.verification import (
    AsyncVerificationServiceAdapter,
    VerificationOutcome,
    VerificationPolicy,
    VerificationResult,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)


def _strict_result_fixture() -> tuple[
    VerificationService,
    VerificationResult,
]:
    service = VerificationService(require_canonical_results=True)
    policy = service.register_policy(
        VerificationPolicy(
            name="strict async result boundary",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
        )
    )
    task_id = new_id("task")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="1",
        digest="sha256:strict-async-result-boundary",
    )
    request = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        result_id=subject.subject_id,
        correlation_id=task_id,
    )
    result = VerificationResult(
        verification_id=request.verification_id,
        verifier=VerifierIdentity(
            verifier_ref="user:reviewer",
            kind=VerifierKind.HUMAN,
            read_only=True,
        ),
        outcome=VerificationOutcome.PASS,
        subject=subject,
        checks_executed=("human_review",),
    )
    return service, result


def test_async_generic_result_submission_preserves_strict_canonical_guard() -> None:
    async def scenario() -> None:
        service, result = _strict_result_fixture()
        adapter = AsyncVerificationServiceAdapter(service)

        with pytest.raises(ContractError) as captured:
            await adapter.submit_result(result)

        assert captured.value.code is ErrorCode.FORBIDDEN
        assert service.result_for(result.verification_id) is None

    asyncio.run(scenario())


def test_async_canonical_result_submission_remains_explicitly_privileged() -> None:
    async def scenario() -> None:
        service, result = _strict_result_fixture()
        adapter = AsyncVerificationServiceAdapter(service)

        persisted = await adapter.submit_canonical_result(result)

        assert persisted == result
        assert service.result_for(result.verification_id) == result

    asyncio.run(scenario())
