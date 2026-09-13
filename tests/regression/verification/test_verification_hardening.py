from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionStatus,
    OperationContext,
)
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import TaskStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security.authorization import RiskClassification
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    CompletionState,
    DeterministicCheck,
    KernelFileVerificationEvidenceResolver,
    ProducerIdentity,
    ReferenceDeterministicVerifier,
    ReviewerIndependence,
    VerificationCompletionAuthority,
    VerificationEvidenceContext,
    VerificationFailurePolicy,
    VerificationOutcome,
    VerificationPolicy,
    VerificationRequestStatus,
    VerificationResult,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)
from ai_multi_agent_platform.verification.audit import VerificationAuditEventType


def _subject() -> VerificationSubject:
    return VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="1",
        digest="sha256:test",
    )


def _request(
    service: VerificationService,
    policy: VerificationPolicy,
    *,
    producer: ProducerIdentity | None = None,
) -> tuple[str, VerificationSubject, str]:
    task_id = new_id("task")
    subject = _subject()
    request = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id=policy.stages[0].stage_id,
        subject=subject,
        result_id=subject.subject_id,
        correlation_id=task_id,
        producer=producer,
    )
    return request.verification_id, subject, task_id


@pytest.mark.parametrize(
    ("rules", "verifier"),
    [
        (
            ReviewerIndependence(producer_agent_must_differ=True),
            VerifierIdentity(
                verifier_ref="agent:reviewer@1",
                kind=VerifierKind.AGENT,
                agent_id=new_id("agent"),
                agent_revision=1,
                read_only=True,
            ),
        ),
        (
            ReviewerIndependence(model_must_differ=True),
            VerifierIdentity(
                verifier_ref="agent:reviewer@1",
                kind=VerifierKind.AGENT,
                agent_id=new_id("agent"),
                agent_revision=1,
                model_config_id="review-model",
                read_only=True,
            ),
        ),
        (
            ReviewerIndependence(provider_must_differ=True),
            VerifierIdentity(
                verifier_ref="provider:reviewer",
                kind=VerifierKind.PROVIDER,
                provider_id="review-provider",
                read_only=True,
            ),
        ),
        (
            ReviewerIndependence(human_reviewer_must_differ=True),
            VerifierIdentity(
                verifier_ref="user:reviewer",
                kind=VerifierKind.HUMAN,
                read_only=True,
            ),
        ),
    ],
)
def test_independence_fails_closed_without_required_producer_identity(
    rules: ReviewerIndependence,
    verifier: VerifierIdentity,
) -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="fail-closed independence",
            stages=(VerificationStage("review", verifier.kind),),
            independence=rules,
        )
    )
    verification_id, _subject_value, _task_id = _request(service, policy)
    with pytest.raises(ContractError) as error:
        service.validate_verifier(verification_id, verifier)
    assert error.value.code is ErrorCode.FORBIDDEN


def test_human_separation_and_generic_self_verification_are_policy_enforced() -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="separate human",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            independence=ReviewerIndependence(
                human_reviewer_must_differ=True,
                forbid_self_verification=True,
            ),
        )
    )
    verification_id, subject, task_id = _request(
        service,
        policy,
        producer=ProducerIdentity(actor_ref="user:producer"),
    )
    with pytest.raises(ContractError) as self_review:
        service.record_human_review(
            verification_id,
            reviewer_ref="user:producer",
            outcome=VerificationOutcome.PASS,
        )
    assert self_review.value.code is ErrorCode.FORBIDDEN
    service.record_human_review(
        verification_id,
        reviewer_ref="user:reviewer",
        outcome=VerificationOutcome.PASS,
    )
    assert (
        service.assess_completion(
            task_id=task_id,
            subject=subject,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        ).state
        is CompletionState.ACCEPTED
    )


def test_n_independent_reviewers_require_n_distinct_identities() -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="two reviewers",
            stages=(
                VerificationStage(
                    "review",
                    VerifierKind.HUMAN,
                    minimum_results=2,
                ),
            ),
            independence=ReviewerIndependence(require_distinct_verifiers=True),
        )
    )
    task_id = new_id("task")
    subject = _subject()

    def create(correlation: str) -> str:
        return service.request_verification(
            task_id=task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject=subject,
            result_id=subject.subject_id,
            correlation_id=correlation,
        ).verification_id

    first = create("first")
    second = create("second")
    service.record_human_review(first, reviewer_ref="user:a", outcome=VerificationOutcome.PASS)
    service.record_human_review(second, reviewer_ref="user:a", outcome=VerificationOutcome.PASS)
    assert (
        service.assess_completion(
            task_id=task_id,
            subject=subject,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        ).state
        is CompletionState.WAITING
    )
    third = create("third")
    service.record_human_review(third, reviewer_ref="user:b", outcome=VerificationOutcome.PASS)
    assert (
        service.assess_completion(
            task_id=task_id,
            subject=subject,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        ).state
        is CompletionState.ACCEPTED
    )


@pytest.mark.parametrize(
    ("timeout_policy", "expected"),
    [
        (VerificationFailurePolicy.WAIT, CompletionState.WAITING),
        (VerificationFailurePolicy.FAIL, CompletionState.REJECTED),
        (VerificationFailurePolicy.ESCALATE, CompletionState.ESCALATED),
    ],
)
def test_request_timeout_uses_explicit_policy_semantics(
    timeout_policy: VerificationFailurePolicy,
    expected: CompletionState,
) -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="timeout policy",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            request_timeout_seconds=5,
            timeout_failure_policy=timeout_policy,
        )
    )
    created = datetime(2026, 9, 4, 1, 0, tzinfo=UTC)
    verification_id, subject, task_id = _request(service, policy)
    request = service.get_request(verification_id)
    # Re-create with a fixed time so the timeout boundary is deterministic.
    service = VerificationService()
    service.register_policy(policy)
    request = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        result_id=subject.subject_id,
        correlation_id="timeout",
        now=created,
    )
    decision = service.assess_completion(
        task_id=task_id,
        subject=subject,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        now=created + timedelta(seconds=6),
    )
    assert (
        service.get_request(request.verification_id, now=created + timedelta(seconds=6)).status
        is VerificationRequestStatus.EXPIRED
    )
    assert decision.state is expected
    assert decision.blocking_verification_ids == (request.verification_id,)


def test_request_cancellation_is_idempotent_auditable_and_terminal() -> None:
    service = VerificationService()
    policy = service.register_policy(
        VerificationPolicy(
            name="cancel",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
        )
    )
    verification_id, _subject_value, _task_id = _request(service, policy)
    cancelled = service.cancel_request(verification_id, causation_id="user-cancel")
    assert cancelled.status is VerificationRequestStatus.CANCELLED
    assert service.cancel_request(verification_id) == cancelled
    assert [
        event.event_type for event in service.audit_history(verification_id=verification_id)
    ] == [
        VerificationAuditEventType.REQUESTED,
        VerificationAuditEventType.REQUEST_CANCELLED,
    ]
    with pytest.raises(ContractError) as completed:
        service.record_human_review(
            verification_id,
            reviewer_ref="user:reviewer",
            outcome=VerificationOutcome.PASS,
        )
    assert completed.value.code is ErrorCode.CONFLICT


def test_result_subject_is_derived_from_terminal_canonical_run_output() -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=repository,
        )
        task = await kernel.create_task(
            idempotency_key="subject:create",
            title="Canonical subject",
            objective="Resolve exact Result evidence",
            owner_type="user",
            owner_id="issue-86",
        )
        await kernel.ready_task(idempotency_key="subject:ready", task_id=task.task_id)
        run = await kernel.start_task(idempotency_key="subject:start", task_id=task.task_id)
        lifecycle.complete(
            run.run_id,
            status=ExecutionStatus.SUCCEEDED,
            output={"answer": 42, "nested": {"ok": True}},
        )
        await kernel.refresh_run(
            idempotency_key="subject:refresh",
            task_id=task.task_id,
            run_id=run.run_id,
        )
        result_id = new_id("result")
        await kernel.attach_result(
            idempotency_key="subject:result",
            task_id=task.task_id,
            run_id=run.run_id,
            result_id=result_id,
        )
        files = LocalFileProvider(
            Path("/tmp") / new_id("file"), Path("/tmp") / f"{new_id('file')}.sqlite"
        )
        resolver = KernelFileVerificationEvidenceResolver(kernel, repository, files)
        resolved = await resolver.resolve_subject(
            task_id=task.task_id,
            subject_type="result",
            subject_id=result_id,
        )
        assert resolved.subject_id == result_id
        assert resolved.revision == f"{run.run_id}:attempt:1"
        assert resolved.digest.startswith("sha256:")
        assert resolved != VerificationSubject(
            subject_type="result",
            subject_id=result_id,
            revision="forged",
            digest="sha256:forged",
        )

    asyncio.run(scenario())


def test_file_backed_artifact_subject_and_evidence_validate_real_checksum(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        )
        task = await kernel.create_task(
            idempotency_key="artifact:create",
            title="Artifact evidence",
            objective="Resolve canonical file-backed Artifact",
            owner_type="user",
            owner_id="issue-86",
        )
        artifact_id = new_id("artifact")
        await kernel.attach_artifact(
            idempotency_key="artifact:attach",
            task_id=task.task_id,
            artifact_id=artifact_id,
        )
        files_root = tmp_path / "files"
        files = LocalFileProvider(files_root, tmp_path / "files.sqlite")
        context = DataAccessContext(
            operation=OperationContext(
                correlation_id=task.task_id,
                owner_type="user",
                owner_id="issue-86",
            ),
            actor_ref="user:issue-86",
            task_id=task.task_id,
        )
        record = await files.create_file(b"canonical evidence", context)
        await files.link_artifact(record.file_id, artifact_id, context)
        resolver = KernelFileVerificationEvidenceResolver(kernel, repository, files)
        subject = await resolver.resolve_subject(
            task_id=task.task_id,
            subject_type="artifact",
            subject_id=artifact_id,
        )
        assert subject.revision == record.file_id
        assert subject.digest == f"sha256:{record.sha256}"
        assert await resolver.validate_evidence_artifacts(
            task_id=task.task_id,
            artifact_ids=(artifact_id,),
        ) == (artifact_id,)

        (files_root / record.file_id).write_bytes(b"tampered")
        with pytest.raises(ContractError) as mismatch:
            await resolver.resolve_subject(
                task_id=task.task_id,
                subject_type="artifact",
                subject_id=artifact_id,
            )
        assert mismatch.value.code is ErrorCode.CONTRACT_VIOLATION

    asyncio.run(scenario())


def test_single_node_deployment_enables_durable_verification_end_to_end(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
        deployment = build_single_node_deployment(config)
        admin = deployment.bootstrap_admin("admin", "correct horse battery staple")
        assert "verifications" in deployment.control_plane.registered_collections
        assert "verification-reviews" in deployment.control_plane.registered_collections
        assert "verification-requirements" in deployment.control_plane.registered_collections

        policy = deployment.verification.register_policy(
            VerificationPolicy(
                name="single-node required review",
                stages=(VerificationStage("review", VerifierKind.HUMAN),),
            )
        )
        task = await deployment.kernel.create_task(
            idempotency_key="verification-deploy:create",
            title="Deployment verification",
            objective="Prove standard deployment gates completion",
            owner_type="user",
            owner_id=admin.user_id,
        )
        deployment.verification_runtime.require_task(
            task_id=task.task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        )
        await deployment.kernel.ready_task(
            idempotency_key="verification-deploy:ready",
            task_id=task.task_id,
        )
        run = await deployment.kernel.start_task(
            idempotency_key="verification-deploy:start",
            task_id=task.task_id,
        )
        await deployment.kernel.refresh_run(
            idempotency_key="verification-deploy:refresh",
            task_id=task.task_id,
            run_id=run.run_id,
        )
        blocked = await deployment.kernel.get_task(task.task_id)
        assert blocked.status is TaskStatus.WAITING
        assert blocked.wait_reason == "verification:waiting"

        result_id = new_id("result")
        await deployment.kernel.attach_result(
            idempotency_key="verification-deploy:result",
            task_id=task.task_id,
            run_id=run.run_id,
            result_id=result_id,
        )
        request = await deployment.verification_runtime.request_verification(
            task_id=task.task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            subject_type="result",
            subject_id=result_id,
            correlation_id=task.task_id,
        )
        assert request.subject.digest.startswith("sha256:")
        await deployment.verification_runtime.submit_result(
            VerificationResult(
                verification_id=request.verification_id,
                verifier=VerifierIdentity(
                    verifier_ref=f"user:{admin.user_id}:reviewer",
                    kind=VerifierKind.HUMAN,
                    read_only=True,
                ),
                outcome=VerificationOutcome.PASS,
                subject=request.subject,
                checks_executed=("human_review",),
            )
        )
        completed = await deployment.kernel.complete_task(
            idempotency_key="verification-deploy:complete",
            task_id=task.task_id,
        )
        assert completed.status is TaskStatus.SUCCEEDED

        restarted = build_single_node_deployment(config)
        restored = restarted.verification.get_request(request.verification_id)
        assert restored.subject == request.subject
        assert restarted.verification.result_for(request.verification_id) is not None
        assert (
            restarted.kernel._completion_authority is not None
            and restarted.kernel._completion_authority.assess_task_completion(task.task_id).state
            is CompletionState.ACCEPTED
        )
        assert "verifications" in restarted.control_plane.registered_collections

    asyncio.run(scenario())


class _MutableEvidenceResolver:
    def __init__(self, subject: VerificationSubject) -> None:
        self.subject = subject
        self.validated: list[tuple[str, ...]] = []

    async def resolve_subject(
        self, *, task_id: str, subject_type: str, subject_id: str
    ) -> VerificationSubject:
        del task_id, subject_type, subject_id
        return self.subject

    async def resolve_context(
        self, *, task_id: str, subject_type: str, subject_id: str
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
        self, *, task_id: str, artifact_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        del task_id
        self.validated.append(artifact_ids)
        return artifact_ids


def test_strict_canonical_result_submission_rechecks_current_subject() -> None:
    async def scenario() -> None:
        subject = VerificationSubject(
            subject_type="result",
            subject_id=new_id("result"),
            revision="1",
            digest="sha256:v1",
        )
        evidence = _MutableEvidenceResolver(subject)
        service = VerificationService(
            require_canonical_subjects=True,
            require_canonical_results=True,
        )
        completion = VerificationCompletionAuthority(service)
        runtime = CanonicalVerificationRuntime(completion, evidence)
        policy = service.register_policy(
            VerificationPolicy(
                name="strict-submission",
                stages=(VerificationStage("provider", VerifierKind.PROVIDER),),
            )
        )
        request = await runtime.request_verification(
            task_id=new_id("task"),
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="provider",
            subject_type="result",
            subject_id=subject.subject_id,
            correlation_id="strict-result",
        )
        proposed = VerificationResult(
            verification_id=request.verification_id,
            verifier=VerifierIdentity(
                verifier_ref="provider:domain",
                kind=VerifierKind.PROVIDER,
                provider_id="domain",
            ),
            outcome=VerificationOutcome.PASS,
            subject=subject,
            checks_executed=("domain",),
        )
        with pytest.raises(ContractError) as raw:
            service.submit_result(proposed)
        assert raw.value.code is ErrorCode.FORBIDDEN

        evidence.subject = VerificationSubject(
            subject_type="result",
            subject_id=subject.subject_id,
            revision="2",
            digest="sha256:v2",
        )
        with pytest.raises(ContractError) as stale:
            await runtime.submit_result(proposed)
        assert stale.value.code is ErrorCode.CONTRACT_VIOLATION
        assert service.result_for(request.verification_id) is None

    asyncio.run(scenario())


def test_risk_class_can_selectively_forbid_self_verification() -> None:
    producer = ProducerIdentity(actor_ref="user:same")
    rules = ReviewerIndependence(forbid_self_verification_risk_classes=(RiskClassification.HIGH,))
    same_human = VerifierIdentity(verifier_ref="user:same", kind=VerifierKind.HUMAN)

    high = VerificationService()
    high_policy = high.register_policy(
        VerificationPolicy(
            name="high-risk",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            independence=rules,
            risk_classification=RiskClassification.HIGH,
        )
    )
    high_id, _high_subject, _high_task = _request(high, high_policy, producer=producer)
    with pytest.raises(ContractError) as denied:
        high.validate_verifier(high_id, same_human)
    assert denied.value.code is ErrorCode.FORBIDDEN

    standard = VerificationService()
    standard_policy = standard.register_policy(
        VerificationPolicy(
            name="standard-risk",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
            independence=rules,
            risk_classification=RiskClassification.STANDARD,
        )
    )
    standard_id, _subject_value, _task_id = _request(standard, standard_policy, producer=producer)
    assert standard.validate_verifier(standard_id, same_human) == same_human


def test_strict_deterministic_submission_uses_canonical_runtime() -> None:
    async def scenario() -> None:
        subject = VerificationSubject(
            subject_type="result",
            subject_id=new_id("result"),
            revision="1",
            digest="sha256:deterministic",
        )
        evidence = _MutableEvidenceResolver(subject)
        service = VerificationService(
            require_canonical_subjects=True,
            require_canonical_results=True,
        )
        completion = VerificationCompletionAuthority(service)
        runtime = CanonicalVerificationRuntime(completion, evidence)
        policy = service.register_policy(
            VerificationPolicy(
                name="strict-deterministic",
                stages=(VerificationStage("checks", VerifierKind.DETERMINISTIC),),
            )
        )
        request = await runtime.request_verification(
            task_id=new_id("task"),
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="checks",
            subject_type="result",
            subject_id=subject.subject_id,
            correlation_id="strict-deterministic",
        )
        verifier = ReferenceDeterministicVerifier(
            "deterministic:reference",
            (
                DeterministicCheck(
                    "subject-present", lambda item: bool(item.subject.digest), "missing"
                ),
            ),
        )
        result = await runtime.run_deterministic(request.verification_id, verifier)
        assert result.outcome is VerificationOutcome.PASS
        assert service.result_for(request.verification_id) == result

    asyncio.run(scenario())
