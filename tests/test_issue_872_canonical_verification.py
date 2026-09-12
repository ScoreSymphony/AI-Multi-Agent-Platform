from __future__ import annotations

import pytest

from ai_multi_agent_platform.agents import (
    AgentRevisionRef,
    AgentRunRecord,
    AgentRunStatus,
    new_agent_id,
    new_agent_run_id,
)
from ai_multi_agent_platform.coding_batches import (
    CanonicalCodingVerificationCoordinator,
    CodingBatchCoordinator,
    CodingWorkItem,
    VerificationEvidence,
    WorkstreamResult,
    WorkstreamState,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories import RepositoryRunProvenance
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    ProducerIdentity,
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


class _AgentRuns:
    def __init__(self, record: AgentRunRecord) -> None:
        self.record = record

    def get_agent_run(self, agent_run_id: str) -> AgentRunRecord:
        if agent_run_id != self.record.agent_run_id:
            raise KeyError(agent_run_id)
        return self.record


class _RepositoryProvenance:
    def __init__(self, record: RepositoryRunProvenance) -> None:
        self.record = record

    def get(self, run_id: str, repository_id: str) -> RepositoryRunProvenance | None:
        if run_id == self.record.run_id and repository_id == self.record.repository_id:
            return self.record
        return None


class _VerificationEvidenceResolver:
    def __init__(
        self,
        *,
        task_id: str,
        run_id: str,
        project_id: str,
        producer: ProducerIdentity,
        subjects: dict[str, VerificationSubject],
    ) -> None:
        self.task_id = task_id
        self.run_id = run_id
        self.project_id = project_id
        self.producer = producer
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
            project_id=self.project_id,
            capability_ids=(),
            producer=self.producer,
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


def _fixture() -> tuple[
    CodingBatchCoordinator,
    str,
    str,
    AgentRunRecord,
    RepositoryRunProvenance,
    tuple[str, str],
]:
    coordinator = CodingBatchCoordinator()
    task_id = new_id("task")
    plan_id = new_id("plan")
    step_id = new_id("step")
    repository_id = new_id("external_resource")
    run_id = new_id("run")
    agent_id = new_agent_id()
    agent_run_id = new_agent_run_id()
    artifacts = (new_id("artifact"), new_id("artifact"))
    base_revision = "1" * 40
    output_revision = "2" * 40
    batch = coordinator.create_batch(
        request_key="issue-872-canonical-verification",
        repository_id=repository_id,
        target_ref="main",
        base_revision=base_revision,
        work_items=(
            CodingWorkItem(
                work_item_id="A",
                task_id=task_id,
                plan_id=plan_id,
                step_id=step_id,
                affected_paths=("src/a.py",),
                semantic_scopes=("module:a",),
            ),
        ),
    )
    coordinator.materialize_workstream(
        batch.batch_id,
        "A",
        workspace_id=new_id("workspace"),
        snapshot_id=new_id("snapshot"),
        agent_revision=f"{agent_id}@3",
        agent_run_id=agent_run_id,
    )
    coordinator.start_workstream(batch.batch_id, "A")
    coordinator.record_result(
        batch.batch_id,
        "A",
        WorkstreamResult(
            output_revision=output_revision,
            changed_paths=("src/a.py",),
            diff_digest="sha256:workstream-diff",
            artifact_ids=artifacts,
        ),
    )
    agent_run = AgentRunRecord(
        agent_run_id=agent_run_id,
        run_id=run_id,
        task_id=task_id,
        agent=AgentRevisionRef(agent_id=agent_id, revision=3),
        status=AgentRunStatus.SUCCEEDED,
        artifact_ids=artifacts,
    )
    repository = RepositoryRunProvenance(
        run_id=run_id,
        repository_id=repository_id,
        input_revision=base_revision,
        output_revision=output_revision,
        actor_ref="agent-runtime",
        agent_id=agent_id,
        task_id=task_id,
        diff_artifact_ids=artifacts,
    )
    return coordinator, batch.batch_id, task_id, agent_run, repository, artifacts


def _review(
    coordinator: CodingBatchCoordinator,
    batch_id: str,
    task_id: str,
    agent_run: AgentRunRecord,
    repository: RepositoryRunProvenance,
    artifacts: tuple[str, str],
) -> tuple[
    CanonicalCodingVerificationCoordinator,
    CanonicalVerificationRuntime,
    VerificationService,
    VerificationPolicy,
]:
    policy = VerificationPolicy(
        name="coding-workstream-review",
        stages=(VerificationStage("review", VerifierKind.DETERMINISTIC),),
    )
    verification = VerificationService(
        require_canonical_subjects=True,
        require_canonical_results=True,
    )
    verification.register_policy(policy)
    completion = VerificationCompletionAuthority(verification)
    producer = ProducerIdentity(
        actor_ref=f"agent:{agent_run.agent.agent_id}@{agent_run.agent.revision}",
        agent_id=agent_run.agent.agent_id,
        agent_revision=agent_run.agent.revision,
    )
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
        completion,
        _VerificationEvidenceResolver(
            task_id=task_id,
            run_id=agent_run.run_id,
            project_id=new_id("project"),
            producer=producer,
            subjects=subjects,
        ),
    )
    review = CanonicalCodingVerificationCoordinator(
        coordinator,
        agent_runs=_AgentRuns(agent_run),
        repository_provenance=_RepositoryProvenance(repository),
        verification_runtime=runtime,
        verification=verification,
    )
    return review, runtime, verification, policy


@pytest.mark.asyncio
async def test_canonical_verification_reuses_request_and_binds_full_diff_to_output_revision() -> None:
    coordinator, batch_id, task_id, agent_run, repository, artifacts = _fixture()
    review, runtime, verification, policy = _review(
        coordinator,
        batch_id,
        task_id,
        agent_run,
        repository,
        artifacts,
    )

    first = await review.ensure_request(
        batch_id,
        "A",
        subject_artifact_id=artifacts[0],
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        correlation_id="issue-872-review",
        causation_id="issue-872-review-A",
    )
    replay = await review.ensure_request(
        batch_id,
        "A",
        subject_artifact_id=artifacts[0],
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        correlation_id="issue-872-review",
        causation_id="issue-872-review-A",
    )
    assert replay.verification_id == first.verification_id

    canonical = verification.get_request(first.verification_id)
    submitted = await runtime.submit_result(
        VerificationResult(
            verification_id=canonical.verification_id,
            verifier=VerifierIdentity(
                verifier_ref="deterministic:issue-872",
                kind=VerifierKind.DETERMINISTIC,
                read_only=True,
            ),
            outcome=VerificationOutcome.PASS,
            subject=canonical.subject,
            evidence_artifact_ids=(artifacts[1],),
            checks_executed=("repository_tests", "diff_review"),
        )
    )

    workstream = review.record_completed(
        batch_id,
        "A",
        verification_id=canonical.verification_id,
    )
    assert workstream.state is WorkstreamState.VERIFIED
    assert workstream.verification == VerificationEvidence(
        verification_id=canonical.verification_id,
        subject_revision=repository.output_revision,
        passed=True,
        check_refs=(submitted.verification_result_id,),
    )

    replayed = review.record_completed(
        batch_id,
        "A",
        verification_id=canonical.verification_id,
    )
    assert replayed == workstream


@pytest.mark.asyncio
async def test_canonical_verification_rejects_partial_diff_artifact_coverage() -> None:
    coordinator, batch_id, task_id, agent_run, repository, artifacts = _fixture()
    review, runtime, verification, policy = _review(
        coordinator,
        batch_id,
        task_id,
        agent_run,
        repository,
        artifacts,
    )
    request = await review.ensure_request(
        batch_id,
        "A",
        subject_artifact_id=artifacts[0],
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        correlation_id="issue-872-review",
    )
    await runtime.submit_result(
        VerificationResult(
            verification_id=request.verification_id,
            verifier=VerifierIdentity(
                verifier_ref="deterministic:issue-872",
                kind=VerifierKind.DETERMINISTIC,
            ),
            outcome=VerificationOutcome.PASS,
            subject=request.subject,
            evidence_artifact_ids=(),
        )
    )

    with pytest.raises(ValueError, match="complete workstream diff-artifact set"):
        review.record_completed(
            batch_id,
            "A",
            verification_id=request.verification_id,
        )


def test_canonical_verification_rejects_repository_output_revision_mismatch() -> None:
    coordinator, batch_id, task_id, agent_run, repository, artifacts = _fixture()
    mismatched = RepositoryRunProvenance(
        run_id=repository.run_id,
        repository_id=repository.repository_id,
        input_revision=repository.input_revision,
        output_revision="3" * 40,
        actor_ref=repository.actor_ref,
        agent_id=repository.agent_id,
        task_id=repository.task_id,
        diff_artifact_ids=repository.diff_artifact_ids,
    )
    review, _runtime, _verification, _policy = _review(
        coordinator,
        batch_id,
        task_id,
        agent_run,
        mismatched,
        artifacts,
    )

    with pytest.raises(ValueError, match="output revision differs"):
        review.record_completed(
            batch_id,
            "A",
            verification_id=new_id("verification"),
        )
