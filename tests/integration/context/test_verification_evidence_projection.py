"""Verification-to-Context projection coverage originating in issue #680."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.context import (
    ContextDataClassification,
    ContextEntryRole,
    ContextFreshness,
    ContextSourceType,
    ContextTrust,
    OperationalContextSourceRequest,
    VerificationContextSourceAdapter,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.verification import (
    VerificationFinding,
    VerificationOutcome,
    VerificationPolicy,
    VerificationResult,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)


def _source_request(*, task_id: str, project_id: str) -> OperationalContextSourceRequest:
    return OperationalContextSourceRequest(
        task_id=task_id,
        run_id=new_id("run"),
        agent_id=new_id("agent"),
        agent_revision=1,
        project_id=project_id,
        workspace_id=None,
        operation=OperationContext(
            correlation_id=task_id,
            owner_type="user",
            owner_id="issue-680",
            project_id=project_id,
        ),
        actor_ref="user:issue-680",
    )


class _FixedVerificationClassificationResolver:
    def __init__(self, classification: ContextDataClassification) -> None:
        self.classification = classification

    async def classify(self, source_request, verification_request, result):
        del source_request, verification_request, result
        return self.classification


def test_completed_verification_findings_project_as_exact_untrusted_evidence() -> None:
    task_id = new_id("task")
    project_id = new_id("project")
    source_run_id = new_id("run")
    result_id = new_id("result")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=result_id,
        revision="result-revision-3",
        digest="a" * 64,
    )
    policy = VerificationPolicy(
        name="Issue 680 verification context",
        stages=(
            VerificationStage(
                stage_id="quality",
                verifier_kind=VerifierKind.DETERMINISTIC,
            ),
        ),
    )
    verification = VerificationService()
    verification.register_policy(policy)
    verification_request = verification.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="quality",
        subject=subject,
        correlation_id=task_id,
        run_id=source_run_id,
        result_id=result_id,
        project_id=project_id,
    )
    verification_result = VerificationResult(
        verification_id=verification_request.verification_id,
        verifier=VerifierIdentity(
            verifier_ref="deterministic:issue-680",
            kind=VerifierKind.DETERMINISTIC,
            read_only=True,
        ),
        outcome=VerificationOutcome.NEEDS_CHANGES,
        subject=subject,
        findings=(
            VerificationFinding(
                code="missing-evidence",
                message="Add exact repository evidence before retrying.",
                severity="warning",
                location_ref="result:summary",
            ),
        ),
        checks_executed=("issue_680_context_projection",),
        metadata={"private_adapter_note": "must-not-be-copied"},
    )
    verification.submit_result(verification_result)
    source_request = _source_request(task_id=task_id, project_id=project_id)

    fail_closed = asyncio.run(
        VerificationContextSourceAdapter(verification).collect(source_request)
    )[0]
    assert fail_closed.data_classification is ContextDataClassification.SECRET_REFERENCE
    assert fail_closed.inline_content is None
    assert fail_closed.content_ref == (
        f"verification-result:{verification_result.verification_result_id}"
    )
    assert fail_closed.metadata == {}

    candidate = asyncio.run(
        VerificationContextSourceAdapter(
            verification,
            classification_resolver=_FixedVerificationClassificationResolver(
                ContextDataClassification.RESTRICTED
            ),
        ).collect(source_request)
    )[0]

    assert candidate.source.source_type is ContextSourceType.VERIFICATION
    assert candidate.source.source_id == verification_request.verification_id
    assert candidate.source.revision == verification_result.verification_result_id
    assert candidate.source.digest == candidate.content_digest
    assert candidate.source.locator == (
        f"verification-result:{verification_result.verification_result_id}"
    )
    assert candidate.role is ContextEntryRole.EVIDENCE
    assert candidate.trust is ContextTrust.UNTRUSTED
    assert candidate.freshness is ContextFreshness.CURRENT
    assert candidate.data_classification is ContextDataClassification.RESTRICTED
    assert candidate.project_id == project_id

    payload = json.loads(candidate.inline_content or "{}")
    assert payload["verification_id"] == verification_request.verification_id
    assert payload["verification_result_id"] == verification_result.verification_result_id
    assert payload["run_id"] == source_run_id
    assert payload["subject"] == {
        "type": "result",
        "id": result_id,
        "revision": "result-revision-3",
        "digest": "a" * 64,
    }
    assert payload["outcome"] == VerificationOutcome.NEEDS_CHANGES.value
    assert payload["findings"] == [
        {
            "code": "missing-evidence",
            "message": "Add exact repository evidence before retrying.",
            "severity": "warning",
            "location_ref": "result:summary",
        }
    ]
    assert "private_adapter_note" not in (candidate.inline_content or "")
    assert "must-not-be-copied" not in (candidate.inline_content or "")


def test_expired_verification_result_projects_as_stale_evidence() -> None:
    now = datetime(2026, 9, 9, 19, 30, tzinfo=UTC)
    completed_at = now - timedelta(seconds=10)
    task_id = new_id("task")
    project_id = new_id("project")
    result_id = new_id("result")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=result_id,
        revision="1",
        digest="c" * 64,
    )
    policy = VerificationPolicy(
        name="Expiring verification",
        stages=(VerificationStage(stage_id="quality", verifier_kind=VerifierKind.DETERMINISTIC),),
        result_expiry_seconds=5,
    )
    verification = VerificationService()
    verification.register_policy(policy)
    request = verification.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="quality",
        subject=subject,
        correlation_id=task_id,
        result_id=result_id,
        project_id=project_id,
    )
    verification.submit_result(
        VerificationResult(
            verification_id=request.verification_id,
            verifier=VerifierIdentity(
                verifier_ref="deterministic:expiry",
                kind=VerifierKind.DETERMINISTIC,
            ),
            outcome=VerificationOutcome.PASS,
            subject=subject,
            started_at=completed_at,
            completed_at=completed_at,
        )
    )

    candidate = asyncio.run(
        VerificationContextSourceAdapter(
            verification,
            classification_resolver=_FixedVerificationClassificationResolver(
                ContextDataClassification.RESTRICTED
            ),
            now=lambda: now,
        ).collect(_source_request(task_id=task_id, project_id=project_id))
    )[0]

    assert candidate.freshness is ContextFreshness.STALE
    assert candidate.data_classification is ContextDataClassification.RESTRICTED


def test_pending_verification_request_does_not_become_context_evidence() -> None:
    task_id = new_id("task")
    project_id = new_id("project")
    result_id = new_id("result")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=result_id,
        revision="1",
        digest="b" * 64,
    )
    policy = VerificationPolicy(
        name="Pending verification",
        stages=(VerificationStage(stage_id="quality", verifier_kind=VerifierKind.HUMAN),),
    )
    verification = VerificationService()
    verification.register_policy(policy)
    verification.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="quality",
        subject=subject,
        correlation_id=task_id,
        result_id=result_id,
        project_id=project_id,
    )

    candidates = asyncio.run(
        VerificationContextSourceAdapter(verification).collect(
            _source_request(task_id=task_id, project_id=project_id)
        )
    )
    assert candidates == ()
