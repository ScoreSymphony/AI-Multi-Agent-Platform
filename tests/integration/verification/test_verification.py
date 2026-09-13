from __future__ import annotations

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.verification import (
    CompletionState,
    DeterministicCheck,
    ProducerIdentity,
    ReferenceDeterministicVerifier,
    ReviewerIndependence,
    VerificationOutcome,
    VerificationPolicy,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)


def subject(
    *,
    result_id: str | None = None,
    revision: str = "1",
    digest: str = "sha256:a",
) -> VerificationSubject:
    return VerificationSubject(
        subject_type="result",
        subject_id=result_id or new_id("result"),
        revision=revision,
        digest=digest,
    )


def test_no_verification_policy_accepts_exact_subject() -> None:
    service = VerificationService()
    task_id = new_id("task")
    assessment = service.assess_completion(task_id=task_id, subject=subject())
    assert assessment.state is CompletionState.ACCEPTED


def test_deterministic_reference_verifier_passes_and_fails_without_llm() -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="deterministic",
            stages=(VerificationStage("tests", VerifierKind.DETERMINISTIC),),
        )
    )
    task_id = new_id("task")
    exact = subject()
    request = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="tests",
        subject=exact,
        result_id=exact.subject_id,
        correlation_id="corr-pass",
    )
    service.run_deterministic(
        request.verification_id,
        ReferenceDeterministicVerifier(
            "deterministic:pytest",
            (DeterministicCheck("tests", lambda _request: True, "tests failed"),),
        ),
    )
    assert (
        service.assess_completion(
            task_id=task_id,
            subject=exact,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        ).state
        is CompletionState.ACCEPTED
    )

    failed_task = new_id("task")
    failed_subject = subject()
    failed_request = service.request_verification(
        task_id=failed_task,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="tests",
        subject=failed_subject,
        result_id=failed_subject.subject_id,
        correlation_id="corr-fail",
    )
    failed = service.run_deterministic(
        failed_request.verification_id,
        ReferenceDeterministicVerifier(
            "deterministic:pytest",
            (DeterministicCheck("tests", lambda _request: False, "tests failed"),),
        ),
    )
    assert failed.outcome is VerificationOutcome.FAIL
    assert (
        service.assess_completion(
            task_id=failed_task,
            subject=failed_subject,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        ).state
        is CompletionState.REJECTED
    )


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (VerificationOutcome.PASS, CompletionState.ACCEPTED),
        (VerificationOutcome.FAIL, CompletionState.REJECTED),
        (VerificationOutcome.INCONCLUSIVE, CompletionState.WAITING),
    ],
)
def test_human_review_uses_canonical_outcomes(
    outcome: VerificationOutcome,
    expected: CompletionState,
) -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="human",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
        )
    )
    task_id = new_id("task")
    exact = subject()
    request = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=exact,
        result_id=exact.subject_id,
        correlation_id="corr-human",
    )
    service.record_human_review(
        request.verification_id,
        reviewer_ref="user:reviewer",
        outcome=outcome,
        comment="reviewed exact result",
    )
    assert (
        service.assess_completion(
            task_id=task_id,
            subject=exact,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        ).state
        is expected
    )


def test_changed_result_revision_cannot_reuse_old_verification() -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="human",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
        )
    )
    task_id = new_id("task")
    result_id = new_id("result")
    original = subject(result_id=result_id, revision="1", digest="sha256:old")
    request = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=original,
        result_id=result_id,
        correlation_id="corr-original",
    )
    service.record_human_review(
        request.verification_id,
        reviewer_ref="user:reviewer",
        outcome=VerificationOutcome.PASS,
    )
    changed = subject(result_id=result_id, revision="2", digest="sha256:new")
    assessment = service.assess_completion(
        task_id=task_id,
        subject=changed,
        policy_id=policy.policy_id,
        policy_version=policy.version,
    )
    assert assessment.state is CompletionState.WAITING


def test_agent_reviewer_independence_and_read_only_rules_are_enforced() -> None:
    service = VerificationService()
    producer_agent = new_id("agent")
    reviewer_agent = new_id("agent")
    policy = service.register_policy(
        VerificationPolicy(
            name="independent-agent-review",
            stages=(VerificationStage("review", VerifierKind.AGENT),),
            independence=ReviewerIndependence(
                producer_agent_must_differ=True,
                agent_reviewer_must_be_read_only=True,
            ),
        )
    )
    task_id = new_id("task")
    exact = subject()
    request = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=exact,
        result_id=exact.subject_id,
        correlation_id="corr-agent",
        producer=ProducerIdentity(
            actor_ref=f"agent:{producer_agent}",
            agent_id=producer_agent,
            agent_revision=1,
        ),
    )
    with pytest.raises(ContractError) as same_agent:
        service.record_agent_review(
            request.verification_id,
            verifier=VerifierIdentity(
                verifier_ref=f"agent:{producer_agent}:1",
                kind=VerifierKind.AGENT,
                agent_id=producer_agent,
                agent_revision=1,
                read_only=True,
            ),
            outcome=VerificationOutcome.PASS,
        )
    assert same_agent.value.code is ErrorCode.FORBIDDEN

    with pytest.raises(ContractError) as writable:
        service.record_agent_review(
            request.verification_id,
            verifier=VerifierIdentity(
                verifier_ref=f"agent:{reviewer_agent}:1",
                kind=VerifierKind.AGENT,
                agent_id=reviewer_agent,
                agent_revision=1,
                read_only=False,
            ),
            outcome=VerificationOutcome.PASS,
        )
    assert writable.value.code is ErrorCode.FORBIDDEN

    service.record_agent_review(
        request.verification_id,
        verifier=VerifierIdentity(
            verifier_ref=f"agent:{reviewer_agent}:1",
            kind=VerifierKind.AGENT,
            agent_id=reviewer_agent,
            agent_revision=1,
            read_only=True,
        ),
        outcome=VerificationOutcome.PASS,
    )
    assert (
        service.assess_completion(
            task_id=task_id,
            subject=exact,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        ).state
        is CompletionState.ACCEPTED
    )


def test_bounded_repair_preserves_history_and_stops_at_policy_limit() -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="repair-once",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            max_repair_attempts=1,
        )
    )
    task_id = new_id("task")
    result_id = new_id("result")
    first_subject = subject(result_id=result_id, revision="1", digest="sha256:first")
    first = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=first_subject,
        result_id=result_id,
        correlation_id="corr-first",
    )
    service.record_human_review(
        first.verification_id,
        reviewer_ref="user:reviewer",
        outcome=VerificationOutcome.NEEDS_CHANGES,
        comment="fix it",
    )
    first_assessment = service.assess_completion(
        task_id=task_id,
        subject=first_subject,
        policy_id=policy.policy_id,
        policy_version=policy.version,
    )
    assert first_assessment.state is CompletionState.REPAIR_REQUIRED
    assert first_assessment.repair_attempts_remaining == 1

    repaired_subject = subject(result_id=result_id, revision="2", digest="sha256:second")
    second = service.request_reverification_after_repair(
        first.verification_id,
        new_subject=repaired_subject,
        result_id=result_id,
        correlation_id="corr-second",
    )
    assert second.repair_attempt == 1
    service.record_human_review(
        second.verification_id,
        reviewer_ref="user:reviewer",
        outcome=VerificationOutcome.NEEDS_CHANGES,
        comment="still wrong",
    )
    exhausted = service.assess_completion(
        task_id=task_id,
        subject=repaired_subject,
        policy_id=policy.policy_id,
        policy_version=policy.version,
    )
    assert exhausted.state is CompletionState.REJECTED
    assert exhausted.repair_attempts_remaining == 0
    assert len(service.history(task_id=task_id)) == 2

    with pytest.raises(ContractError) as limit:
        service.request_reverification_after_repair(
            second.verification_id,
            new_subject=subject(result_id=result_id, revision="3", digest="sha256:third"),
            result_id=result_id,
            correlation_id="corr-third",
        )
    assert limit.value.code is ErrorCode.CONFLICT


def test_later_critical_stage_failure_overrides_earlier_incomplete_stage() -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="multi-stage critical ordering",
            stages=(
                VerificationStage("human", VerifierKind.HUMAN),
                VerificationStage("tests", VerifierKind.DETERMINISTIC, critical=True),
            ),
        )
    )
    task_id = new_id("task")
    exact = subject()
    service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="human",
        subject=exact,
        result_id=exact.subject_id,
        correlation_id="human-pending",
    )
    failed = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="tests",
        subject=exact,
        result_id=exact.subject_id,
        correlation_id="tests-fail",
    )
    service.run_deterministic(
        failed.verification_id,
        ReferenceDeterministicVerifier(
            "deterministic:critical",
            (DeterministicCheck("tests", lambda _request: False, "critical tests failed"),),
        ),
    )
    decision = service.assess_completion(
        task_id=task_id,
        subject=exact,
        policy_id=policy.policy_id,
        policy_version=policy.version,
    )
    assert decision.state is CompletionState.REJECTED
    assert decision.blocking_verification_ids == (failed.verification_id,)


def test_agent_revisions_do_not_count_as_distinct_reviewers() -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="two independent agent reviewers",
            stages=(VerificationStage("review", VerifierKind.AGENT, minimum_results=2),),
            independence=ReviewerIndependence(require_distinct_verifiers=True),
        )
    )
    task_id = new_id("task")
    exact = subject()
    same_agent = new_id("agent")

    def request(correlation: str) -> str:
        return service.request_verification(
            task_id=task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=exact,
            result_id=exact.subject_id,
            correlation_id=correlation,
        ).verification_id

    for revision in (1, 2):
        service.record_agent_review(
            request(f"same-agent-{revision}"),
            verifier=VerifierIdentity(
                verifier_ref=f"agent:{same_agent}@{revision}",
                kind=VerifierKind.AGENT,
                agent_id=same_agent,
                agent_revision=revision,
                read_only=True,
            ),
            outcome=VerificationOutcome.PASS,
        )
    waiting = service.assess_completion(
        task_id=task_id,
        subject=exact,
        policy_id=policy.policy_id,
        policy_version=policy.version,
    )
    assert waiting.state is CompletionState.WAITING

    other_agent = new_id("agent")
    service.record_agent_review(
        request("other-agent"),
        verifier=VerifierIdentity(
            verifier_ref=f"agent:{other_agent}@1",
            kind=VerifierKind.AGENT,
            agent_id=other_agent,
            agent_revision=1,
            read_only=True,
        ),
        outcome=VerificationOutcome.PASS,
    )
    assert (
        service.assess_completion(
            task_id=task_id,
            subject=exact,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        ).state
        is CompletionState.ACCEPTED
    )
