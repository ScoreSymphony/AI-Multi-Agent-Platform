from __future__ import annotations

import pytest

from ai_multi_agent_platform.coding_batches import (
    CanonicalCombinedValidationCoordinator,
    CanonicalRepairVerificationCoordinator,
    CanonicalRepositoryOutputVerifier,
    CheckState,
    CodingBatchCoordinator,
    CodingBatchRepairCoordinator,
    CodingWorkItem,
    InMemoryCodingBatchStore,
    IntegrationState,
    RequiredCheck,
    VerificationEvidence,
    WorkstreamResult,
)
from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, new_id
from ai_multi_agent_platform.repositories import RepositoryRunProvenance
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    VerificationCompletionAuthority,
    VerificationEvidenceContext,
    VerificationOutcome,
    VerificationPolicy,
    VerificationResult,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
)

BASE = "a" * 40
WORKSTREAM_REVISION = "b" * 40
INTEGRATED = "c" * 40
REPAIRED = "d" * 40


class _RepositoryProvenance:
    def __init__(self, records: tuple[RepositoryRunProvenance, ...]) -> None:
        self.records = records

    def get(self, run_id: str, repository_id: str) -> RepositoryRunProvenance | None:
        for record in self.records:
            if record.run_id == run_id and record.repository_id == repository_id:
                return record
        return None


class _EvidenceResolver:
    def __init__(
        self,
        *,
        task_id: str,
        run_id: str,
        subjects: dict[str, VerificationSubject],
    ) -> None:
        self.task_id = task_id
        self.run_id = run_id
        self.subjects = subjects

    async def resolve_subject(
        self,
        *,
        task_id: str,
        subject_type: str,
        subject_id: str,
    ) -> VerificationSubject:
        assert task_id == self.task_id
        assert subject_type == "artifact"
        return self.subjects[subject_id]

    async def resolve_context(
        self,
        *,
        task_id: str,
        subject_type: str,
        subject_id: str,
    ) -> VerificationEvidenceContext:
        subject = await self.resolve_subject(
            task_id=task_id,
            subject_type=subject_type,
            subject_id=subject_id,
        )
        return VerificationEvidenceContext(
            task_id=task_id,
            subject=subject,
            run_id=self.run_id,
            project_id=new_id("project"),
            capability_ids=(),
            producer=None,
        )

    async def validate_evidence_artifacts(
        self,
        *,
        task_id: str,
        artifact_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        assert task_id == self.task_id
        assert all(artifact_id in self.subjects for artifact_id in artifact_ids)
        return artifact_ids


def _verification(
    *,
    task_id: str,
    run_id: str,
    artifacts: tuple[str, str],
) -> tuple[
    CanonicalVerificationRuntime,
    VerificationService,
    VerificationPolicy,
]:
    service = VerificationService(
        require_canonical_subjects=True,
        require_canonical_results=True,
    )
    policy = VerificationPolicy(
        name="issue-872-repository-output",
        stages=(VerificationStage("review", VerifierKind.DETERMINISTIC),),
    )
    service.register_policy(policy)
    subjects = {
        artifact_id: VerificationSubject(
            subject_type="artifact",
            subject_id=artifact_id,
            revision=new_id("file"),
            digest=f"sha256:{index:064x}",
        )
        for index, artifact_id in enumerate(artifacts, start=1)
    }
    runtime = CanonicalVerificationRuntime(
        VerificationCompletionAuthority(service),
        _EvidenceResolver(task_id=task_id, run_id=run_id, subjects=subjects),
    )
    return runtime, service, policy


def _accepted_single_workstream(
    store: InMemoryCodingBatchStore,
    *,
    task_id: str,
    repository_id: str,
) -> tuple[CodingBatchCoordinator, str, str]:
    coordinator = CodingBatchCoordinator(store)
    batch = coordinator.create_batch(
        request_key="issue-872-final-verification",
        repository_id=repository_id,
        target_ref="main",
        base_revision=BASE,
        work_items=(
            CodingWorkItem(
                work_item_id="A",
                task_id=task_id,
                plan_id=new_id("plan"),
                step_id=new_id("step"),
                affected_paths=("src/a.py",),
            ),
        ),
    )
    coordinator.materialize_workstream(
        batch.batch_id,
        "A",
        workspace_id=new_id("workspace"),
        snapshot_id=new_id("snapshot"),
        agent_revision="agent-a@1",
        agent_run_id="agent-run-a",
    )
    coordinator.start_workstream(batch.batch_id, "A")
    coordinator.record_result(
        batch.batch_id,
        "A",
        WorkstreamResult(
            output_revision=WORKSTREAM_REVISION,
            changed_paths=("src/a.py",),
            diff_digest="digest-a",
        ),
    )
    coordinator.record_verification(
        batch.batch_id,
        "A",
        VerificationEvidence(
            verification_id="verification-a",
            subject_revision=WORKSTREAM_REVISION,
            passed=True,
        ),
    )
    coordinator.accept_workstream(batch.batch_id, "A")
    candidate = coordinator.build_integration_candidate(
        batch.batch_id,
        current_target_revision=BASE,
    )
    return coordinator, batch.batch_id, candidate.integration_id


@pytest.mark.asyncio
async def test_combined_validation_requires_complete_canonical_82_86_evidence() -> None:
    store = InMemoryCodingBatchStore()
    task_id = new_id("task")
    repository_id = new_id("external_resource")
    run_id = new_id("run")
    artifacts = (new_id("artifact"), new_id("artifact"))
    coordinator, batch_id, integration_id = _accepted_single_workstream(
        store,
        task_id=task_id,
        repository_id=repository_id,
    )
    coordinator.record_integrated_revision(
        batch_id,
        integration_id,
        integrated_revision=INTEGRATED,
    )
    repository = RepositoryRunProvenance(
        run_id=run_id,
        repository_id=repository_id,
        input_revision=BASE,
        output_revision=INTEGRATED,
        actor_ref="integration-runtime",
        task_id=task_id,
        diff_artifact_ids=artifacts,
    )
    runtime, service, policy = _verification(
        task_id=task_id,
        run_id=run_id,
        artifacts=artifacts,
    )
    verifier = CanonicalRepositoryOutputVerifier(
        repository_provenance=_RepositoryProvenance((repository,)),
        verification_runtime=runtime,
        verification=service,
    )
    combined = CanonicalCombinedValidationCoordinator(coordinator, verifier)
    request = await combined.ensure_request(
        batch_id,
        integration_id,
        integration_task_id=task_id,
        integration_run_id=run_id,
        subject_artifact_id=artifacts[0],
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        correlation_id="issue-872-combined",
        causation_id="issue-872-combined-exact",
    )
    replay = await combined.ensure_request(
        batch_id,
        integration_id,
        integration_task_id=task_id,
        integration_run_id=run_id,
        subject_artifact_id=artifacts[0],
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        correlation_id="issue-872-combined",
        causation_id="issue-872-combined-exact",
    )
    assert replay.verification_id == request.verification_id

    await runtime.submit_result(
        VerificationResult(
            verification_id=request.verification_id,
            verifier=VerifierIdentity(
                verifier_ref="deterministic:issue-872-combined",
                kind=VerifierKind.DETERMINISTIC,
            ),
            outcome=VerificationOutcome.PASS,
            subject=request.subject,
            evidence_artifact_ids=(artifacts[1],),
            checks_executed=("combined-tests", "diff-review"),
        )
    )
    validated = combined.record_completed(
        batch_id,
        integration_id,
        integration_task_id=task_id,
        integration_run_id=run_id,
        verification_id=request.verification_id,
        tests_passed=True,
        required_checks=(RequiredCheck("ci", INTEGRATED, CheckState.PASS),),
    )
    assert validated.state is IntegrationState.VALIDATED
    assert validated.validation is not None
    assert validated.validation.subject_revision == INTEGRATED
    assert validated.validation.verification_id == request.verification_id


@pytest.mark.asyncio
async def test_repair_output_cannot_unblock_until_full_canonical_verification_passes() -> None:
    store = InMemoryCodingBatchStore()
    task_id = new_id("task")
    repository_id = new_id("external_resource")
    coordinator = CodingBatchCoordinator(store)
    batch = coordinator.create_batch(
        request_key="issue-872-repair-final-verification",
        repository_id=repository_id,
        target_ref="main",
        base_revision=BASE,
        work_items=(
            CodingWorkItem(
                work_item_id="A",
                task_id=task_id,
                plan_id=new_id("plan"),
                step_id=new_id("step"),
                affected_paths=("src/a.py",),
                semantic_scopes=("shared-contract",),
            ),
            CodingWorkItem(
                work_item_id="B",
                task_id=task_id,
                plan_id=new_id("plan"),
                step_id=new_id("step"),
                affected_paths=("src/b.py",),
                semantic_scopes=("shared-contract",),
            ),
        ),
    )
    for workstream_id, revision, path in (
        ("A", "1" * 40, "src/a.py"),
        ("B", "2" * 40, "src/b.py"),
    ):
        coordinator.materialize_workstream(
            batch.batch_id,
            workstream_id,
            workspace_id=new_id("workspace"),
            snapshot_id=new_id("snapshot"),
            agent_revision=f"agent-{workstream_id}@1",
            agent_run_id=f"agent-run-{workstream_id}",
        )
        coordinator.start_workstream(batch.batch_id, workstream_id)
        coordinator.record_result(
            batch.batch_id,
            workstream_id,
            WorkstreamResult(revision, (path,), f"digest-{workstream_id}"),
        )
        coordinator.record_verification(
            batch.batch_id,
            workstream_id,
            VerificationEvidence(
                f"verification-{workstream_id}",
                revision,
                True,
            ),
        )
        coordinator.accept_workstream(batch.batch_id, workstream_id)
    candidate = coordinator.build_integration_candidate(
        batch.batch_id,
        current_target_revision=BASE,
    )
    assert candidate.state is IntegrationState.BLOCKED

    owner = OwnerRef(type="user", id="issue-872-repair-verification")
    repair_plan = Plan(task_id=task_id, owner_ref=owner, active=True)
    repair_step = Step(plan_id=repair_plan.id, title="repair", owner_ref=owner)
    repair = CodingBatchRepairCoordinator(store)
    attempt = repair.bind_repair_step(
        batch.batch_id,
        candidate.integration_id,
        repair_plan=repair_plan,
        repair_step=repair_step,
        target_revision=BASE,
    )
    repair_run_id = new_id("run")
    artifacts = (new_id("artifact"), new_id("artifact"))
    repository = RepositoryRunProvenance(
        run_id=repair_run_id,
        repository_id=repository_id,
        input_revision=BASE,
        output_revision=REPAIRED,
        actor_ref="repair-runtime",
        task_id=task_id,
        diff_artifact_ids=artifacts,
    )
    runtime, service, policy = _verification(
        task_id=task_id,
        run_id=repair_run_id,
        artifacts=artifacts,
    )
    verifier = CanonicalRepositoryOutputVerifier(
        repository_provenance=_RepositoryProvenance((repository,)),
        verification_runtime=runtime,
        verification=service,
    )
    canonical_repair = CanonicalRepairVerificationCoordinator(store, repair, verifier)
    request = await canonical_repair.ensure_request(
        batch.batch_id,
        candidate.integration_id,
        attempt.repair_id,
        repair_run_id=repair_run_id,
        subject_artifact_id=artifacts[0],
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        correlation_id="issue-872-repair",
    )
    await runtime.submit_result(
        VerificationResult(
            verification_id=request.verification_id,
            verifier=VerifierIdentity(
                verifier_ref="deterministic:issue-872-repair",
                kind=VerifierKind.DETERMINISTIC,
            ),
            outcome=VerificationOutcome.PASS,
            subject=request.subject,
            evidence_artifact_ids=(artifacts[1],),
        )
    )

    repaired = canonical_repair.record_completed(
        batch.batch_id,
        candidate.integration_id,
        attempt.repair_id,
        repair_run_id=repair_run_id,
        verification_id=request.verification_id,
    )
    assert repaired.state is IntegrationState.VALIDATING
    assert repaired.integrated_revision == REPAIRED
    assert repaired.repair_attempts[-1].verification is not None
    assert repaired.repair_attempts[-1].verification.verification_id == request.verification_id
