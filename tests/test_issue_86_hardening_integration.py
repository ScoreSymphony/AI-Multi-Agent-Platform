from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.verification import (
    SqliteVerificationService,
    VerificationOutcome,
    VerificationPolicy,
    VerificationRequestStatus,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)
from ai_multi_agent_platform.verification.audit import VerificationAuditEventType


def test_cancelled_verification_request_survives_sqlite_restart(tmp_path: Path) -> None:
    path = tmp_path / "verification.sqlite3"
    service = SqliteVerificationService(path)
    policy = service.register_policy(
        VerificationPolicy(
            name="durable cancellation",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
        )
    )
    task_id = new_id("task")
    result_id = new_id("result")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=result_id,
        revision="1",
        digest="sha256:durable-cancel",
    )
    request = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        result_id=result_id,
        correlation_id=task_id,
    )
    cancelled = service.cancel_request(
        request.verification_id,
        causation_id="cancel-before-restart",
    )
    assert cancelled.status is VerificationRequestStatus.CANCELLED

    restored = SqliteVerificationService(path)
    assert (
        restored.get_request(request.verification_id).status is VerificationRequestStatus.CANCELLED
    )
    assert restored.result_for(request.verification_id) is None
    assert [
        event.event_type
        for event in restored.audit_history(verification_id=request.verification_id)
    ] == [
        VerificationAuditEventType.REQUESTED,
        VerificationAuditEventType.REQUEST_CANCELLED,
    ]


def test_single_node_human_review_rejects_forged_subject_and_unknown_evidence(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", "correct horse battery staple")
        policy = deployment.verification.register_policy(
            VerificationPolicy(
                name="canonical evidence review",
                stages=(VerificationStage("review", VerifierKind.HUMAN),),
            )
        )
        task = await deployment.kernel.create_task(
            idempotency_key="canonical-review:create",
            title="Canonical evidence review",
            objective="Reject forged review bindings and unknown evidence",
            owner_type="user",
            owner_id=admin.user_id,
        )
        await deployment.kernel.ready_task(
            idempotency_key="canonical-review:ready",
            task_id=task.task_id,
        )
        run = await deployment.kernel.start_task(
            idempotency_key="canonical-review:start",
            task_id=task.task_id,
        )
        await deployment.kernel.refresh_run(
            idempotency_key="canonical-review:refresh",
            task_id=task.task_id,
            run_id=run.run_id,
        )
        result_id = new_id("result")
        await deployment.kernel.attach_result(
            idempotency_key="canonical-review:result",
            task_id=task.task_id,
            run_id=run.run_id,
            result_id=result_id,
        )

        canonical = await deployment.verification_runtime.evidence.resolve_subject(
            task_id=task.task_id,
            subject_type="result",
            subject_id=result_id,
        )
        forged = VerificationSubject(
            subject_type="result",
            subject_id=result_id,
            revision=canonical.revision,
            digest="sha256:forged",
        )
        with pytest.raises(ContractError) as forged_error:
            deployment.verification.request_verification(
                task_id=task.task_id,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                stage_id="review",
                subject=forged,
                correlation_id=task.task_id,
                run_id=run.run_id,
                result_id=result_id,
            )
        assert forged_error.value.code is ErrorCode.FORBIDDEN

        canonical_request = await deployment.verification_runtime.request_verification(
            task_id=task.task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject_type="result",
            subject_id=result_id,
            correlation_id=task.task_id,
        )
        unknown_artifact = new_id("artifact")
        evidence_context = RequestContext(
            request_id="canonical-review-evidence",
            correlation_id=task.task_id,
            idempotency_key="canonical-review-evidence",
            actor=ActorContext(
                principal_ref=admin.user_id,
                owner_type="user",
                owner_id=admin.user_id,
                actor_type="human",
            ),
        )
        with pytest.raises(ContractError) as evidence_error:
            await deployment.control_plane.execute_command(
                evidence_context,
                "verification.accept",
                canonical_request.verification_id,
                {"evidence_artifact_ids": [unknown_artifact]},
            )
        assert evidence_error.value.code is ErrorCode.NOT_FOUND
        assert deployment.verification.result_for(canonical_request.verification_id) is None

        deployment.verification.record_human_review(
            canonical_request.verification_id,
            reviewer_ref=admin.user_id,
            outcome=VerificationOutcome.PASS,
        )

    asyncio.run(scenario())
