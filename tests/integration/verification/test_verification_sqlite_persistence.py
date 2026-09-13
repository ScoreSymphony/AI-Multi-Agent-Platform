from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import Provenance, new_id
from ai_multi_agent_platform.verification import (
    CompletionState,
    SqliteVerificationCompletionAuthority,
    SqliteVerificationService,
    VerificationOutcome,
    VerificationPolicy,
    VerificationRequestStatus,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)


def result_subject(
    *,
    result_id: str | None = None,
    revision: str = "1",
    digest: str = "sha256:result-v1",
) -> VerificationSubject:
    return VerificationSubject(
        subject_type="result",
        subject_id=result_id or new_id("result"),
        revision=revision,
        digest=digest,
    )


def test_sqlite_restart_restores_policy_request_result_and_completion_requirement(tmp_path) -> None:
    path = tmp_path / "verification.sqlite"
    service = SqliteVerificationService(path)
    authority = SqliteVerificationCompletionAuthority(service, path)
    policy = service.register_policy(
        VerificationPolicy(
            name="durable-human-review",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            provenance=Provenance(
                source="test",
                actor_ref="user:owner",
                details={"issue": 86},
            ),
            metadata={"risk": "high"},
        )
    )
    task_id = new_id("task")
    exact = result_subject()
    request = authority.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=exact,
        result_id=exact.subject_id,
        correlation_id=task_id,
    )
    recorded = service.record_human_review(
        request.verification_id,
        reviewer_ref="user:reviewer",
        outcome=VerificationOutcome.PASS,
        comment="accepted after durable review",
    )
    assert authority.assess_task_completion(task_id).state is CompletionState.ACCEPTED

    restored_service = SqliteVerificationService(path)
    restored_authority = SqliteVerificationCompletionAuthority(restored_service, path)

    assert restored_service.get_policy(policy.policy_id, policy.version) == policy
    restored_request = restored_service.get_request(request.verification_id)
    assert restored_request.status is VerificationRequestStatus.COMPLETED
    assert restored_service.result_for(request.verification_id) == recorded
    restored_requirement = restored_authority.requirement_for(task_id)
    assert restored_requirement is not None
    assert restored_requirement.subject == exact
    assert restored_authority.assess_task_completion(task_id).state is CompletionState.ACCEPTED
    history = restored_service.history(task_id=task_id)
    assert history == ((restored_request, recorded),)


def test_expired_pending_request_remains_expired_after_restart(tmp_path) -> None:
    path = tmp_path / "verification.sqlite"
    service = SqliteVerificationService(path)
    policy = service.register_policy(
        VerificationPolicy(
            name="short-lived",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            request_timeout_seconds=10,
        )
    )
    task_id = new_id("task")
    exact = result_subject()
    created_at = datetime(2026, 9, 3, 20, 0, tzinfo=UTC)
    request = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=exact,
        result_id=exact.subject_id,
        correlation_id=task_id,
        now=created_at,
    )
    expired = service.get_request(
        request.verification_id,
        now=created_at + timedelta(seconds=11),
    )
    assert expired.status is VerificationRequestStatus.EXPIRED

    restored = SqliteVerificationService(path)
    assert restored.get_request(request.verification_id).status is VerificationRequestStatus.EXPIRED


def test_repair_history_and_new_exact_subject_survive_restart(tmp_path) -> None:
    path = tmp_path / "verification.sqlite"
    service = SqliteVerificationService(path)
    authority = SqliteVerificationCompletionAuthority(service, path)
    policy = service.register_policy(
        VerificationPolicy(
            name="bounded-repair",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            max_repair_attempts=1,
        )
    )
    task_id = new_id("task")
    result_id = new_id("result")
    original = result_subject(result_id=result_id, revision="1", digest="sha256:old")
    first = authority.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=original,
        result_id=result_id,
        correlation_id=task_id,
    )
    service.record_human_review(
        first.verification_id,
        reviewer_ref="user:reviewer",
        outcome=VerificationOutcome.NEEDS_CHANGES,
        comment="repair required",
    )

    repaired = result_subject(result_id=result_id, revision="2", digest="sha256:new")
    second = authority.request_reverification_after_repair(
        first.verification_id,
        new_subject=repaired,
        correlation_id=task_id,
        result_id=result_id,
    )
    service.record_human_review(
        second.verification_id,
        reviewer_ref="user:reviewer",
        outcome=VerificationOutcome.PASS,
    )

    restored_service = SqliteVerificationService(path)
    restored_authority = SqliteVerificationCompletionAuthority(restored_service, path)
    restored_requirement = restored_authority.requirement_for(task_id)
    assert restored_requirement is not None
    assert restored_requirement.subject == repaired
    assert restored_authority.assess_task_completion(task_id).state is CompletionState.ACCEPTED
    history = restored_service.history(task_id=task_id)
    assert len(history) == 2
    first_result = history[0][1]
    second_result = history[1][1]
    assert first_result is not None
    assert first_result.outcome is VerificationOutcome.NEEDS_CHANGES
    assert history[1][0].repair_attempt == 1
    assert second_result is not None
    assert second_result.outcome is VerificationOutcome.PASS


def test_restart_reconciles_newer_request_before_subject_binding(tmp_path) -> None:
    path = tmp_path / "verification.sqlite"
    service = SqliteVerificationService(path)
    authority = SqliteVerificationCompletionAuthority(service, path)
    policy = service.register_policy(
        VerificationPolicy(
            name="crash-safe-human-review",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
        )
    )
    task_id = new_id("task")
    result_id = new_id("result")
    original = result_subject(result_id=result_id, revision="1", digest="sha256:accepted")
    first = authority.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=original,
        result_id=result_id,
        correlation_id=task_id,
    )
    service.record_human_review(
        first.verification_id,
        reviewer_ref="user:reviewer",
        outcome=VerificationOutcome.PASS,
    )
    assert authority.assess_task_completion(task_id).state is CompletionState.ACCEPTED

    changed = result_subject(result_id=result_id, revision="2", digest="sha256:changed")
    service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=changed,
        result_id=result_id,
        correlation_id=task_id,
    )

    restored_service = SqliteVerificationService(path)
    restored_authority = SqliteVerificationCompletionAuthority(restored_service, path)
    restored_requirement = restored_authority.requirement_for(task_id)
    assert restored_requirement is not None
    assert restored_requirement.subject == changed
    assert restored_authority.assess_task_completion(task_id).state is CompletionState.WAITING

    second_restart_service = SqliteVerificationService(path)
    second_restart_authority = SqliteVerificationCompletionAuthority(second_restart_service, path)
    second_requirement = second_restart_authority.requirement_for(task_id)
    assert second_requirement is not None
    assert second_requirement.subject == changed
    assert second_restart_authority.assess_task_completion(task_id).state is CompletionState.WAITING


def test_corrupt_or_unknown_persisted_schema_fails_closed(tmp_path) -> None:
    path = tmp_path / "verification.sqlite"
    service = SqliteVerificationService(path)
    service.register_policy(VerificationPolicy(name="empty", stages=()))

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE verification_snapshots SET payload = ? WHERE namespace = ?",
            (
                '{"schema_version":"999","policies":[],"requests":[],"results":[]}',
                "verification-service",
            ),
        )

    with pytest.raises(ContractError) as exc_info:
        SqliteVerificationService(path)
    assert exc_info.value.code is ErrorCode.BACKEND_ERROR
