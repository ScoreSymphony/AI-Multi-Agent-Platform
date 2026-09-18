"""Canonical repository and Verification checks for combined and repaired coding-batch outputs."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.repositories import (
    AsyncRepositoryProvenanceGetReader,
    RepositoryRunProvenance,
    as_async_repository_provenance_get_reader,
)
from ai_multi_agent_platform.verification import (
    AsyncVerificationService,
    CanonicalVerificationRuntime,
    VerificationOutcome,
    VerificationRequest,
    VerificationRequestStatus,
    VerificationService,
    runtime_verification_service,
)

from .models import (
    CodingBatch,
    CombinedValidationEvidence,
    IntegrationCandidate,
    IntegrationRepairAttempt,
    IntegrationState,
    RepairAttemptState,
    RequiredCheck,
    VerificationEvidence,
)
from .repair import CodingBatchRepairCoordinator
from .service import CodingBatchCoordinator, CodingBatchStore


class RepositoryRunEvidenceReader(Protocol):
    """Minimal repository-provenance read boundary used to prove exact repository output."""

    def get(self, run_id: str, repository_id: str) -> RepositoryRunProvenance | None: ...


class CanonicalRepositoryOutputVerifier:
    """Translate a canonical repository Run output into exact passing Verification evidence.

    A Git SHA is not itself a Verification subject. The bridge requires the complete
    repository diff-artifact set for the Run, verifies one Artifact as the primary subject,
    and requires the completed result to cover every remaining diff Artifact. The returned
    ``VerificationEvidence`` is safe to project because its revision comes only from the
    proven repository output revision.
    """

    def __init__(
        self,
        *,
        repository_provenance: RepositoryRunEvidenceReader,
        verification_runtime: CanonicalVerificationRuntime,
        verification: VerificationService,
        runtime_verification: AsyncVerificationService | None = None,
        runtime_repository_provenance: AsyncRepositoryProvenanceGetReader | None = None,
    ) -> None:
        self._repository_provenance = repository_provenance
        self._runtime_repository_provenance = (
            runtime_repository_provenance
            or as_async_repository_provenance_get_reader(repository_provenance)
        )
        self._runtime = verification_runtime
        self._verification = verification
        self._runtime_verification = runtime_verification_service(
            verification,
            runtime_service=runtime_verification,
        )

    def provenance(
        self,
        *,
        task_id: str,
        repository_id: str,
        run_id: str,
        expected_input_revision: str,
        expected_output_revision: str | None = None,
    ) -> RepositoryRunProvenance:
        record = self._repository_provenance.get(run_id, repository_id)
        return self._validate_provenance(
            record,
            task_id=task_id,
            expected_input_revision=expected_input_revision,
            expected_output_revision=expected_output_revision,
        )

    async def _runtime_provenance(
        self,
        *,
        task_id: str,
        repository_id: str,
        run_id: str,
        expected_input_revision: str,
        expected_output_revision: str | None = None,
    ) -> RepositoryRunProvenance:
        record = await self._runtime_repository_provenance.get(run_id, repository_id)
        return self._validate_provenance(
            record,
            task_id=task_id,
            expected_input_revision=expected_input_revision,
            expected_output_revision=expected_output_revision,
        )

    @staticmethod
    def _validate_provenance(
        record: RepositoryRunProvenance | None,
        *,
        task_id: str,
        expected_input_revision: str,
        expected_output_revision: str | None,
    ) -> RepositoryRunProvenance:
        if record is None:
            raise ValueError("canonical repository Run provenance is missing")
        if record.task_id != task_id:
            raise ValueError("repository output provenance belongs to another Task")
        if record.input_revision != expected_input_revision:
            raise ValueError("repository input revision differs from expected integration base")
        if (
            expected_output_revision is not None
            and record.output_revision != expected_output_revision
        ):
            raise ValueError("repository output revision differs from expected integrated revision")
        if record.output_revision is None:
            raise ValueError("canonical repository Run has no output revision")
        if not record.diff_artifact_ids:
            raise ValueError("canonical repository Run has no diff artifacts to verify")
        return record

    async def ensure_request(
        self,
        *,
        task_id: str,
        repository_id: str,
        run_id: str,
        expected_input_revision: str,
        subject_artifact_id: str,
        policy_id: str,
        policy_version: int,
        stage_id: str,
        correlation_id: str,
        expected_output_revision: str | None = None,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        repository = await self._runtime_provenance(
            task_id=task_id,
            repository_id=repository_id,
            run_id=run_id,
            expected_input_revision=expected_input_revision,
            expected_output_revision=expected_output_revision,
        )
        if subject_artifact_id not in repository.diff_artifact_ids:
            raise ValueError(
                "Verification subject is outside the canonical repository diff-artifact set"
            )
        subject = await self._runtime.evidence.resolve_subject(
            task_id=task_id,
            subject_type="artifact",
            subject_id=subject_artifact_id,
        )

        exact: list[VerificationRequest] = []
        for request, _result in await self._runtime_verification.history(task_id=task_id):
            same_route = (
                request.policy_id == policy_id
                and request.policy_version == policy_version
                and request.stage_id == stage_id
            )
            if same_route and request.subject == subject and request.run_id == run_id:
                exact.append(request)
                continue
            if (
                same_route
                and causation_id is not None
                and request.causation_id == causation_id
                and (request.subject != subject or request.run_id != run_id)
            ):
                raise ValueError(
                    "Verification retry causation already belongs to different repository evidence"
                )
        if len(exact) > 1:
            raise ValueError("repository output maps to multiple canonical Verification requests")
        if exact:
            self._validate_request(exact[0], repository, task_id=task_id, run_id=run_id)
            return exact[0]

        request = await self._runtime.request_verification(
            task_id=task_id,
            policy_id=policy_id,
            policy_version=policy_version,
            stage_id=stage_id,
            subject_type="artifact",
            subject_id=subject_artifact_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        self._validate_request(request, repository, task_id=task_id, run_id=run_id)
        return request

    def completed_evidence(
        self,
        *,
        task_id: str,
        repository_id: str,
        run_id: str,
        expected_input_revision: str,
        verification_id: str,
        expected_output_revision: str | None = None,
    ) -> VerificationEvidence:
        repository = self.provenance(
            task_id=task_id,
            repository_id=repository_id,
            run_id=run_id,
            expected_input_revision=expected_input_revision,
            expected_output_revision=expected_output_revision,
        )
        request = self._verification.get_request(verification_id)
        self._validate_request(request, repository, task_id=task_id, run_id=run_id)
        if request.status is not VerificationRequestStatus.COMPLETED:
            raise ValueError("canonical Verification request is not completed")
        result = self._verification.result_for(verification_id)
        if result is None:
            raise ValueError("completed canonical Verification is missing its result")
        if result.verification_id != request.verification_id or result.subject != request.subject:
            raise ValueError("canonical Verification result does not match its exact request")
        if result.outcome is not VerificationOutcome.PASS:
            raise ValueError("repository output requires a passing canonical Verification")
        covered_artifacts = {request.subject.subject_id, *result.evidence_artifact_ids}
        if covered_artifacts != set(repository.diff_artifact_ids):
            raise ValueError(
                "canonical Verification must cover the complete repository diff-artifact set"
            )
        assert repository.output_revision is not None
        return VerificationEvidence(
            verification_id=request.verification_id,
            subject_revision=repository.output_revision,
            passed=True,
            check_refs=(result.verification_result_id,),
        )

    @staticmethod
    def _validate_request(
        request: VerificationRequest,
        repository: RepositoryRunProvenance,
        *,
        task_id: str,
        run_id: str,
    ) -> None:
        if request.task_id != task_id or request.run_id != run_id:
            raise ValueError("canonical Verification is bound to another Task/Run")
        if request.subject.subject_type != "artifact":
            raise ValueError("repository output Verification must use a canonical Artifact subject")
        if request.subject.subject_id not in repository.diff_artifact_ids:
            raise ValueError("canonical Verification subject is outside repository diff artifacts")
        if (
            repository.agent_id is not None
            and request.producer is not None
            and request.producer.agent_id != repository.agent_id
        ):
            raise ValueError(
                "canonical Verification producer differs from repository Agent provenance"
            )


class CanonicalCombinedValidationCoordinator:
    """Record combined validation only after the integrated SHA passes Verification."""

    def __init__(
        self,
        coordinator: CodingBatchCoordinator,
        verifier: CanonicalRepositoryOutputVerifier,
    ) -> None:
        self._coordinator = coordinator
        self._verifier = verifier

    async def ensure_request(
        self,
        batch_id: str,
        integration_id: str,
        *,
        integration_task_id: str,
        integration_run_id: str,
        subject_artifact_id: str,
        policy_id: str,
        policy_version: int,
        stage_id: str,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        batch, candidate = self._candidate(batch_id, integration_id)
        if candidate.state is not IntegrationState.VALIDATING:
            raise ValueError("combined Verification requires validating integration state")
        if candidate.integrated_revision is None:
            raise ValueError("combined Verification requires an integrated revision")
        return await self._verifier.ensure_request(
            task_id=integration_task_id,
            repository_id=batch.repository_id,
            run_id=integration_run_id,
            expected_input_revision=candidate.target_base_revision,
            expected_output_revision=candidate.integrated_revision,
            subject_artifact_id=subject_artifact_id,
            policy_id=policy_id,
            policy_version=policy_version,
            stage_id=stage_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    def record_completed(
        self,
        batch_id: str,
        integration_id: str,
        *,
        integration_task_id: str,
        integration_run_id: str,
        verification_id: str,
        tests_passed: bool,
        required_checks: tuple[RequiredCheck, ...],
        evaluation_refs: tuple[str, ...] = (),
    ) -> IntegrationCandidate:
        batch, candidate = self._candidate(batch_id, integration_id)
        if candidate.integrated_revision is None:
            raise ValueError("combined validation requires an integrated revision")
        evidence = self._verifier.completed_evidence(
            task_id=integration_task_id,
            repository_id=batch.repository_id,
            run_id=integration_run_id,
            expected_input_revision=candidate.target_base_revision,
            expected_output_revision=candidate.integrated_revision,
            verification_id=verification_id,
        )
        return self._coordinator.record_combined_validation(
            batch_id,
            integration_id,
            CombinedValidationEvidence(
                subject_revision=evidence.subject_revision,
                verification_id=evidence.verification_id,
                tests_passed=tests_passed,
                required_checks=required_checks,
                evaluation_refs=evaluation_refs,
            ),
        )

    def _candidate(
        self,
        batch_id: str,
        integration_id: str,
    ) -> tuple[CodingBatch, IntegrationCandidate]:
        batch = self._coordinator.get(batch_id)
        return batch, batch.integration_candidate(integration_id)


class CanonicalRepairVerificationCoordinator:
    """Accept repair output only after fresh Verification evidence for its revision."""

    def __init__(
        self,
        store: CodingBatchStore,
        repair: CodingBatchRepairCoordinator,
        verifier: CanonicalRepositoryOutputVerifier,
    ) -> None:
        self._store = store
        self._repair = repair
        self._verifier = verifier

    async def ensure_request(
        self,
        batch_id: str,
        integration_id: str,
        repair_id: str,
        *,
        repair_run_id: str,
        subject_artifact_id: str,
        policy_id: str,
        policy_version: int,
        stage_id: str,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> VerificationRequest:
        batch, repair = self._repair_attempt(batch_id, integration_id, repair_id)
        return await self._verifier.ensure_request(
            task_id=repair.task_id,
            repository_id=batch.repository_id,
            run_id=repair_run_id,
            expected_input_revision=repair.target_revision,
            subject_artifact_id=subject_artifact_id,
            policy_id=policy_id,
            policy_version=policy_version,
            stage_id=stage_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    def record_completed(
        self,
        batch_id: str,
        integration_id: str,
        repair_id: str,
        *,
        repair_run_id: str,
        verification_id: str,
    ) -> IntegrationCandidate:
        batch, repair = self._repair_attempt(batch_id, integration_id, repair_id)
        evidence = self._verifier.completed_evidence(
            task_id=repair.task_id,
            repository_id=batch.repository_id,
            run_id=repair_run_id,
            expected_input_revision=repair.target_revision,
            verification_id=verification_id,
        )
        return self._repair.record_repair_result(
            batch_id,
            integration_id,
            repair_id,
            output_revision=evidence.subject_revision,
            verification=evidence,
        )

    def _repair_attempt(
        self,
        batch_id: str,
        integration_id: str,
        repair_id: str,
    ) -> tuple[CodingBatch, IntegrationRepairAttempt]:
        batch = self._store.get(batch_id)
        if batch is None:
            raise KeyError(batch_id)
        candidate = batch.integration_candidate(integration_id)
        repair = candidate.repair_attempt(repair_id)
        if repair.state not in {RepairAttemptState.BOUND, RepairAttemptState.VERIFIED}:
            raise ValueError("canonical Verification requires an active or verified repair attempt")
        return batch, repair


__all__ = [
    "CanonicalCombinedValidationCoordinator",
    "CanonicalRepairVerificationCoordinator",
    "CanonicalRepositoryOutputVerifier",
    "RepositoryRunEvidenceReader",
]
