from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    PageQuery,
    RequestContext,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.verification import (
    SqliteVerificationService,
    VerificationOutcome,
    VerificationPolicy,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)
from ai_multi_agent_platform.verification.audit import VerificationAuditEventType
from ai_multi_agent_platform.verification.observability import VerificationTimelineReader


def _subject(
    *,
    result_id: str | None = None,
    revision: str = "1",
    digest: str = "sha256:verified-result",
) -> VerificationSubject:
    return VerificationSubject(
        subject_type="result",
        subject_id=result_id or new_id("result"),
        revision=revision,
        digest=digest,
    )


def _human_policy(
    *, max_repair_attempts: int = 0, timeout: float | None = None
) -> VerificationPolicy:
    return VerificationPolicy(
        name="observable-human-review",
        stages=(VerificationStage("review", VerifierKind.HUMAN),),
        max_repair_attempts=max_repair_attempts,
        request_timeout_seconds=timeout,
    )


def test_audit_history_is_ordered_content_safe_and_bound_to_exact_subject() -> None:
    service = VerificationService()
    policy = service.register_policy(_human_policy())
    task_id = new_id("task")
    exact = _subject()
    request = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=exact,
        result_id=exact.subject_id,
        correlation_id="verification-correlation",
    )
    service.record_human_review(
        request.verification_id,
        reviewer_ref="user:reviewer",
        outcome=VerificationOutcome.PASS,
        comment="sensitive reviewer prose must remain outside audit telemetry",
        evidence_artifact_ids=(new_id("artifact"),),
    )

    task_events = service.audit_history(task_id=task_id)
    assert [event.event_type for event in task_events] == [
        VerificationAuditEventType.REQUESTED,
        VerificationAuditEventType.RESULT_RECORDED,
    ]
    assert task_events[0].subject == exact
    assert task_events[1].subject == exact
    assert task_events[1].outcome is VerificationOutcome.PASS
    assert task_events[1].verifier is not None
    assert task_events[1].verifier.verifier_ref == "user:reviewer"
    assert "sensitive reviewer prose" not in repr(task_events)

    global_events = service.audit_history()
    assert global_events[0].event_type is VerificationAuditEventType.POLICY_REGISTERED
    assert global_events[0].policy_id == policy.policy_id


def test_reverification_and_expiry_are_explicit_audit_facts() -> None:
    service = VerificationService()
    policy = service.register_policy(_human_policy(max_repair_attempts=1, timeout=10))
    task_id = new_id("task")
    result_id = new_id("result")
    first_subject = _subject(result_id=result_id, revision="1", digest="sha256:v1")
    first = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=first_subject,
        result_id=result_id,
        correlation_id="repair-correlation",
    )
    service.record_human_review(
        first.verification_id,
        reviewer_ref="user:reviewer",
        outcome=VerificationOutcome.NEEDS_CHANGES,
    )
    second_subject = _subject(result_id=result_id, revision="2", digest="sha256:v2")
    second = service.request_reverification_after_repair(
        first.verification_id,
        new_subject=second_subject,
        correlation_id="repair-correlation",
        result_id=result_id,
    )
    assert second.repair_attempt == 1

    created_at = datetime(2026, 9, 4, 0, 0, tzinfo=UTC)
    expiring_subject = _subject()
    expiring = service.request_verification(
        task_id=new_id("task"),
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=expiring_subject,
        result_id=expiring_subject.subject_id,
        correlation_id="expiry-correlation",
        now=created_at,
    )
    service.get_request(expiring.verification_id, now=created_at + timedelta(seconds=11))

    task_types = [event.event_type for event in service.audit_history(task_id=task_id)]
    assert task_types == [
        VerificationAuditEventType.REQUESTED,
        VerificationAuditEventType.RESULT_RECORDED,
        VerificationAuditEventType.REVERIFICATION_REQUESTED,
    ]
    expiry_types = [
        event.event_type
        for event in service.audit_history(verification_id=expiring.verification_id)
    ]
    assert expiry_types == [
        VerificationAuditEventType.REQUESTED,
        VerificationAuditEventType.REQUEST_EXPIRED,
    ]


def test_sqlite_restart_preserves_audit_without_replaying_duplicate_events(tmp_path) -> None:
    path = tmp_path / "verification.sqlite"
    service = SqliteVerificationService(path)
    policy = service.register_policy(_human_policy())
    task_id = new_id("task")
    exact = _subject()
    request = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=exact,
        result_id=exact.subject_id,
        correlation_id="durable-audit",
    )
    service.record_human_review(
        request.verification_id,
        reviewer_ref="user:reviewer",
        outcome=VerificationOutcome.PASS,
    )
    before = service.audit_history()

    restored = SqliteVerificationService(path)
    after = restored.audit_history()
    assert after == before
    assert restored.get_request(request.verification_id).verification_id == request.verification_id
    assert restored.audit_history() == after


def test_verification_timeline_reader_maps_audit_to_issue_16_semantics() -> None:
    service = VerificationService()
    policy = service.register_policy(_human_policy())
    task_id = new_id("task")
    run_id = new_id("run")
    exact = _subject()
    request = service.request_verification(
        task_id=task_id,
        run_id=run_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=exact,
        result_id=exact.subject_id,
        correlation_id="timeline-correlation",
    )
    service.record_human_review(
        request.verification_id,
        reviewer_ref="user:reviewer",
        outcome=VerificationOutcome.FAIL,
        comment="this comment must not enter telemetry",
    )

    entries = VerificationTimelineReader(service).query_timeline(task_id=task_id)
    assert [entry.event_name for entry in entries] == [
        VerificationAuditEventType.REQUESTED.value,
        VerificationAuditEventType.RESULT_RECORDED.value,
    ]
    recorded = entries[-1]
    assert recorded.component.value == "verification"
    assert recorded.context.task_id == task_id
    assert recorded.context.run_id == run_id
    assert recorded.context.verification_id == request.verification_id
    assert recorded.outcome.value == "failed"
    assert recorded.failure is not None
    assert recorded.failure.code == "verification_failed"
    assert recorded.attributes["subject"] == {
        "type": exact.subject_type,
        "id": exact.subject_id,
        "revision": exact.revision,
        "digest": exact.digest,
    }
    assert "this comment must not enter telemetry" not in repr(entries)


def test_control_plane_timeline_merges_verification_projection_without_changing_kernel_truth() -> (
    None
):
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        )
        control = ControlPlane(kernel=kernel, events=repository)
        task = await kernel.create_task(
            idempotency_key="issue-86-observability-task",
            title="Verification timeline",
            objective="Expose canonical review activity through #16",
            owner_type="user",
            owner_id="verification-observer",
        )

        service = VerificationService()
        policy = service.register_policy(_human_policy())
        exact = _subject()
        request = service.request_verification(
            task_id=task.task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=exact,
            result_id=exact.subject_id,
            correlation_id=task.task_id,
        )
        service.record_human_review(
            request.verification_id,
            reviewer_ref="user:reviewer",
            outcome=VerificationOutcome.PASS,
        )

        control.bind_observability_timeline(VerificationTimelineReader(service))
        page = await control.timeline(
            RequestContext(
                request_id="request-verification-observability",
                correlation_id="request-verification-observability",
                actor=ActorContext(
                    principal_ref="user:verification-observer",
                    owner_type="user",
                    owner_id="verification-observer",
                ),
            ),
            task.task_id,
            PageQuery(sort="timestamp", direction="asc"),
        )
        items = page["items"]
        assert isinstance(items, list)
        assert any(isinstance(item, dict) and item.get("type") == "event" for item in items)
        verification_items = [
            item
            for item in items
            if isinstance(item, dict)
            and item.get("type") == "telemetry"
            and item.get("component") == "verification"
        ]
        assert [item["event_name"] for item in verification_items] == [
            VerificationAuditEventType.REQUESTED.value,
            VerificationAuditEventType.RESULT_RECORDED.value,
        ]
        assert await kernel.get_task(task.task_id) == task

    asyncio.run(scenario())
