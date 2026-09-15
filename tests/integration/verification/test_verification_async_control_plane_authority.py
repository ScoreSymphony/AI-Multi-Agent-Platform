from __future__ import annotations

import asyncio

from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)
from ai_multi_agent_platform.verification import (
    VerificationCompletionAuthority,
    VerificationPolicy,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)
from ai_multi_agent_platform.verification.control_plane import register_verification_control_plane


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Idempotency-Key": "strict-control-plane-review",
        "X-Request-Id": "request-strict-verification",
        "X-Correlation-Id": "correlation-strict-verification",
        "X-Principal-Ref": "user:reviewer",
        "X-Owner-Type": "user",
        "X-Owner-Id": "strict-owner",
    }


def test_control_plane_without_canonical_runtime_cannot_bypass_strict_result_guard() -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        verification = VerificationService(require_canonical_results=True)
        completion = VerificationCompletionAuthority(verification)
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
            completion_authority=completion,
        )
        control_plane = ControlPlane(
            kernel=kernel,
            events=repository,
            authorization=FakeAuthorizationProvider(),
        )
        register_verification_control_plane(control_plane, verification, completion)
        http = ControlPlaneHTTP(control_plane)

        task = await kernel.create_task(
            idempotency_key="create-strict-verification-task",
            title="Strict review",
            objective="Preserve canonical result authority",
            owner_type="user",
            owner_id="strict-owner",
        )
        policy = verification.register_policy(
            VerificationPolicy(
                name="strict-control-plane-review",
                stages=(VerificationStage("human", VerifierKind.HUMAN),),
            )
        )
        result_id = new_id("result")
        subject = VerificationSubject(
            subject_type="result",
            subject_id=result_id,
            revision="1",
            digest="sha256:strict-control-plane-review",
        )
        request = completion.request_verification(
            task_id=task.task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="human",
            subject=subject,
            result_id=result_id,
            correlation_id=task.task_id,
        )

        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/verification.accept",
                headers=_headers(),
                body={"resource_ref": request.verification_id},
            )
        )

        assert response.status == 403
        assert verification.result_for(request.verification_id) is None

    asyncio.run(scenario())
