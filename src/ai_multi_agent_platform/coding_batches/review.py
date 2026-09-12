"""Canonical #33/#82/#86 evidence binding for issue #872 workstream review."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.agents import AgentRunRecord, AgentRunStatus
from ai_multi_agent_platform.repositories import RepositoryRunProvenance
from ai_multi_agent_platform.verification import (
    CanonicalVerificationRuntime,
    ProducerIdentity,
    VerificationOutcome,
    VerificationRequest,
    VerificationRequestStatus,
    VerificationResult,
    VerificationService,
)

from .models import CodingBatch, CodingWorkstream, VerificationEvidence, WorkstreamState
from .service import CodingBatchCoordinator


class AgentRunEvidenceReader(Protocol):
    """Minimal #33 read boundary needed to prove one coding workstream producer."""

    def get_agent_run(self, agent_run_id: str) -> AgentRunRecord: ...


class RepositoryRunEvidenceReader(Protocol):
    """Minimal #82 provenance boundary needed to bind artifacts to an exact output SHA."""

    def get(self, run_id: str, repository_id: str) -> RepositoryRunProvenance | None: ...


class CanonicalCodingVerificationCoordinator:
    """Project canonical #86 review back into #872 only with complete output provenance.

    Verification remains owned by #86. This adapter requests/reuses a canonical Artifact review
    and accepts the resulting evidence only when the Artifact set is the exact #82 diff-artifact
    set for the #33 producer Run and repository output revision recorded by the workstream.
    """

    def __init__(
        self,
        coordinator: CodingBatchCoordinator,
        *,
        agent_runs: AgentRunEvidenceReader,
        repository_provenance: RepositoryRunEvidenceReader,
        verification_runtime: CanonicalVerificationRuntime,
        verification: VerificationService,
    ) -> None:
        self._coordinator = coordinator
        self._agent_runs = agent_runs
        self._repository_provenance = repository_provenance
        self._runtime = verification_runtime
        self._verification = verification

    async def ensure_request(
        self,
        batch_id: str,
        workstream_id: str,
        *,
        subject_artifact_id: str,
        policy_id: str,
        policy_version: int,
        stage_id: str,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        """Create or reuse one exact canonical #86 request for the produced workstream output."""

        batch, workstream, agent_run, _repository = self._output_evidence(
            batch_id,
            workstream_id,
        )
        if workstream.state is not WorkstreamState.PRODUCED:
            raise ValueError("canonical workstream Verification requires produced state")
        assert workstream.result is not None
        if subject_artifact_id not in workstream.result.artifact_ids:
            raise ValueError(
                "Verification subject is not part of the exact workstream diff evidence"
            )

        subject = await self._runtime.evidence.resolve_subject(
            task_id=workstream.work_item.task_id,
            subject_type="artifact",
            subject_id=subject_artifact_id,
        )
        exact: list[VerificationRequest] = []
        for request, _result in self._verification.history(task_id=workstream.work_item.task_id):
            same_route = (
                request.policy_id == policy_id
                and request.policy_version == policy_version
                and request.stage_id == stage_id
            )
            if same_route and request.subject == subject and request.run_id == agent_run.run_id:
                exact.append(request)
                continue
            if (
                same_route
                and causation_id is not None
                and request.causation_id == causation_id
                and (request.subject != subject or request.run_id != agent_run.run_id)
            ):
                raise ValueError(
                    "Verification retry causation already belongs to different canonical evidence"
                )
        if len(exact) > 1:
            raise ValueError("workstream output maps to multiple canonical Verification requests")
        if exact:
            self._validate_request(exact[0], batch, workstream, agent_run)
            return exact[0]

        request = await self._runtime.request_verification(
            task_id=workstream.work_item.task_id,
            policy_id=policy_id,
            policy_version=policy_version,
            stage_id=stage_id,
            subject_type="artifact",
            subject_id=subject_artifact_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        self._validate_request(request, batch, workstream, agent_run)
        return request

    def record_completed(
        self,
        batch_id: str,
        workstream_id: str,
        *,
        verification_id: str,
    ) -> CodingWorkstream:
        """Record a completed #86 result only when it covers the exact #82 diff evidence."""

        batch, workstream, agent_run, repository = self._output_evidence(
            batch_id,
            workstream_id,
        )
        if workstream.state not in {
            WorkstreamState.PRODUCED,
            WorkstreamState.VERIFIED,
            WorkstreamState.FAILED,
        }:
            raise ValueError("workstream is not awaiting canonical Verification")
        assert workstream.result is not None

        request = self._verification.get_request(verification_id)
        self._validate_request(request, batch, workstream, agent_run)
        if request.status is not VerificationRequestStatus.COMPLETED:
            raise ValueError("canonical Verification request is not completed")
        result = self._verification.result_for(verification_id)
        if result is None:
            raise ValueError("completed canonical Verification is missing its result")
        self._validate_result(result, request, workstream, repository)

        evidence = VerificationEvidence(
            verification_id=request.verification_id,
            subject_revision=workstream.result.output_revision,
            passed=result.outcome is VerificationOutcome.PASS,
            check_refs=(result.verification_result_id,),
        )
        return self._coordinator.record_verification(
            batch.batch_id,
            workstream.id,
            evidence,
        )

    def _output_evidence(
        self,
        batch_id: str,
        workstream_id: str,
    ) -> tuple[CodingBatch, CodingWorkstream, AgentRunRecord, RepositoryRunProvenance]:
        batch = self._coordinator.get(batch_id)
        workstream = batch.workstream(workstream_id)
        if workstream.result is None:
            raise ValueError("workstream has no produced repository result")
        if workstream.provenance.agent_run_id is None:
            raise ValueError("workstream has no canonical AgentRun provenance")
        agent_run = self._agent_runs.get_agent_run(workstream.provenance.agent_run_id)
        if agent_run.task_id != workstream.work_item.task_id:
            raise ValueError("canonical AgentRun belongs to another Task")
        if agent_run.status is not AgentRunStatus.SUCCEEDED:
            raise ValueError("canonical Developer AgentRun must succeed before Verification")
        expected_agent_revision = f"{agent_run.agent.agent_id}@{agent_run.agent.revision}"
        if workstream.provenance.agent_revision != expected_agent_revision:
            raise ValueError("workstream Agent revision differs from canonical #33 provenance")

        repository = self._repository_provenance.get(agent_run.run_id, batch.repository_id)
        if repository is None:
            raise ValueError("canonical #82 Run repository provenance is missing")
        if repository.task_id != workstream.work_item.task_id:
            raise ValueError("repository output provenance belongs to another Task")
        if repository.input_revision != workstream.provenance.base_revision:
            raise ValueError("repository input revision differs from workstream base")
        if repository.output_revision != workstream.result.output_revision:
            raise ValueError("repository output revision differs from workstream result")
        if repository.agent_id is not None and repository.agent_id != agent_run.agent.agent_id:
            raise ValueError("repository output provenance names another Agent")

        expected_artifacts = set(repository.diff_artifact_ids)
        recorded_artifacts = set(workstream.result.artifact_ids)
        if not expected_artifacts or recorded_artifacts != expected_artifacts:
            raise ValueError(
                "workstream artifacts must equal the complete canonical #82 diff-artifact set"
            )
        if not recorded_artifacts.issubset(set(agent_run.artifact_ids)):
            raise ValueError("workstream diff artifacts are not owned by the canonical AgentRun")
        return batch, workstream, agent_run, repository

    @staticmethod
    def _validate_request(
        request: VerificationRequest,
        batch: CodingBatch,
        workstream: CodingWorkstream,
        agent_run: AgentRunRecord,
    ) -> None:
        if request.task_id != workstream.work_item.task_id or request.run_id != agent_run.run_id:
            raise ValueError("canonical Verification is bound to another Task/Run")
        if request.subject.subject_type != "artifact":
            raise ValueError("coding workstream Verification must use a canonical Artifact subject")
        assert workstream.result is not None
        if request.subject.subject_id not in workstream.result.artifact_ids:
            raise ValueError("canonical Verification subject is outside workstream diff artifacts")
        producer = request.producer
        expected = ProducerIdentity(
            actor_ref=f"agent:{agent_run.agent.agent_id}@{agent_run.agent.revision}",
            agent_id=agent_run.agent.agent_id,
            agent_revision=agent_run.agent.revision,
            model_config_id=agent_run.selected_model_config_id,
            provider_id=agent_run.selected_provider_id,
        )
        if producer != expected:
            raise ValueError("canonical Verification producer differs from the #33 AgentRun")
        if workstream.provenance.repository_id != batch.repository_id:
            raise ValueError("workstream repository provenance differs from coding batch")

    @staticmethod
    def _validate_result(
        result: VerificationResult,
        request: VerificationRequest,
        workstream: CodingWorkstream,
        repository: RepositoryRunProvenance,
    ) -> None:
        if result.verification_id != request.verification_id or result.subject != request.subject:
            raise ValueError("canonical Verification result does not match its exact request")
        assert workstream.result is not None
        covered_artifacts = {request.subject.subject_id, *result.evidence_artifact_ids}
        expected_artifacts = set(workstream.result.artifact_ids)
        if covered_artifacts != expected_artifacts:
            raise ValueError(
                "canonical Verification must cover the complete workstream diff-artifact set"
            )
        if expected_artifacts != set(repository.diff_artifact_ids):
            raise ValueError("Verification artifact coverage differs from canonical #82 provenance")
