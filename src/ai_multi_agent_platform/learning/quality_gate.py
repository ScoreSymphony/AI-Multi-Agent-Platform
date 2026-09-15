"""Read-only Evaluation and Verification quality gates for governed learning."""

from __future__ import annotations

from typing import Any, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.evaluation import (
    EvaluationOutcome,
    EvaluationRunStatus,
    EvaluationService,
)
from ai_multi_agent_platform.verification import VerificationOutcome, VerificationService

from .models import (
    LearningCandidate,
    LearningReference,
    LearningTarget,
    LearningTargetType,
)

_IDENTITY_BOUND_TARGET_TYPES = frozenset(
    {
        LearningTargetType.AGENT,
        LearningTargetType.SKILL,
        LearningTargetType.MODEL_ROUTING_PROFILE,
    }
)


class LearningQualityGate:
    """Read-only bridge to canonical Evaluation and Verification evidence."""

    def __init__(
        self,
        *,
        evaluation: EvaluationService | None = None,
        verification: VerificationService | None = None,
    ) -> None:
        self.evaluation = evaluation
        self.verification = verification

    def enforce(self, candidate: LearningCandidate) -> None:
        if candidate.gate_plan.require_evaluation:
            self._enforce_evaluation(candidate)
        if candidate.gate_plan.require_verification:
            self._enforce_verification(candidate)

    def _enforce_evaluation(self, candidate: LearningCandidate) -> None:
        if self.evaluation is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "learning candidate requires Evaluation but no Evaluation service is configured",
            )
        if not candidate.evaluation_run_ids:
            raise ContractError(
                ErrorCode.CONFLICT,
                "learning candidate is missing required Evaluation evidence",
            )
        allowed_suites = set(candidate.gate_plan.evaluation_suite_refs)
        for run_id in candidate.evaluation_run_ids:
            detail = self.evaluation.get_run_detail(run_id)
            run = detail.run
            if run.status is not EvaluationRunStatus.COMPLETED:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "learning candidate Evaluation run is not completed",
                    details={"evaluation_run_id": run_id, "status": run.status.value},
                )
            _require_evaluation_manifest(detail, run_id)
            _require_evaluation_target_binding(candidate, detail, run_id)
            suite_ref = f"{run.suite_id}@{run.suite_version}"
            if allowed_suites and suite_ref not in allowed_suites:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "learning candidate Evaluation run uses an unplanned suite",
                    details={"evaluation_run_id": run_id, "suite_ref": suite_ref},
                )
            if not detail.results:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "learning candidate Evaluation run has no result evidence",
                    details={"evaluation_run_id": run_id},
                )
            failures = [
                result.result_id
                for result in detail.results
                if result.outcome is not EvaluationOutcome.PASSED
            ]
            if failures:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "learning candidate failed its Evaluation gate",
                    details={
                        "evaluation_run_id": run_id,
                        "failing_result_ids": cast(JsonValue, failures),
                    },
                )
            if candidate.gate_plan.require_regression_free:
                comparison = detail.comparison
                if comparison is None:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "learning candidate requires a persisted Evaluation comparison",
                        details={"evaluation_run_id": run_id},
                    )
                _require_comparison_identity(run, comparison, run_id)
                if comparison.regressions:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "learning candidate has Evaluation regressions",
                        details={
                            "evaluation_run_id": run_id,
                            "regression_count": len(comparison.regressions),
                        },
                    )

    def _enforce_verification(self, candidate: LearningCandidate) -> None:
        if self.verification is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "learning candidate requires Verification but no Verification "
                "service is configured",
            )
        if not candidate.verification_ids:
            raise ContractError(
                ErrorCode.CONFLICT,
                "learning candidate is missing required Verification evidence",
            )
        allowed_policies = set(candidate.gate_plan.verification_policy_refs)
        for verification_id in candidate.verification_ids:
            request = self.verification.get_request(verification_id)
            if request.project_id != candidate.project_id:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "learning candidate project scope does not match Verification evidence",
                    details={
                        "verification_id": verification_id,
                        "candidate_project_id": candidate.project_id,
                        "verification_project_id": request.project_id,
                    },
                )
            policy_ref = f"{request.policy_id}@{request.policy_version}"
            if allowed_policies and policy_ref not in allowed_policies:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "learning candidate Verification uses an unplanned policy",
                    details={
                        "verification_id": verification_id,
                        "verification_policy_ref": policy_ref,
                    },
                )
            result = self.verification.result_for(verification_id)
            if result is None:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "learning candidate Verification has no completed result",
                    details={"verification_id": verification_id},
                )
            _require_verification_target_binding(
                candidate.target,
                candidate.proposed_artifact_ref,
                request,
                result,
                verification_id,
            )
            if result.outcome is not VerificationOutcome.PASS:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "learning candidate failed its Verification gate",
                    details={
                        "verification_id": verification_id,
                        "outcome": result.outcome.value,
                    },
                )


def _require_evaluation_manifest(detail: Any, evaluation_run_id: str) -> None:
    manifest = getattr(detail, "manifest", None)
    if manifest is None:
        raise ContractError(
            ErrorCode.CONFLICT,
            "learning Evaluation evidence is missing its persisted EvalManifest",
            details={"evaluation_run_id": evaluation_run_id},
        )
    run = detail.run
    if (
        manifest.evaluation_run_id != run.run_id
        or manifest.suite_id != run.suite_id
        or str(manifest.suite_version) != str(run.suite_version)
    ):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "learning Evaluation manifest identity does not match the run",
            details={"evaluation_run_id": evaluation_run_id},
        )


def _require_evaluation_target_binding(
    candidate: LearningCandidate,
    detail: Any,
    evaluation_run_id: str,
) -> None:
    _require_evaluation_target_binding_for_target(candidate.target, detail, evaluation_run_id)
    if candidate.proposed_artifact_ref is not None:
        _require_evaluation_manifest(detail, evaluation_run_id)
        if not _manifest_contains_learning_reference(
            detail.manifest, candidate.proposed_artifact_ref
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Evaluation manifest does not pin the proposed candidate artifact",
                details={"evaluation_run_id": evaluation_run_id},
            )


def _require_evaluation_target_binding_for_target(
    target: LearningTarget,
    detail: Any,
    evaluation_run_id: str,
) -> None:
    if target.resource_type not in _IDENTITY_BOUND_TARGET_TYPES:
        return
    _require_evaluation_manifest(detail, evaluation_run_id)
    expected_ids = {target.resource_id}
    if target.resource_type is LearningTargetType.MODEL_ROUTING_PROFILE:
        expected_ids.add(f"{target.resource_id}@r{target.revision}")
    snapshot_match = _has_exact_target_reference(
        detail.run.snapshot.references,
        target,
        expected_ids,
    )
    manifest_match = _has_exact_target_reference(
        detail.manifest.configuration_references,
        target,
        expected_ids,
    )
    if not snapshot_match or not manifest_match:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Evaluation evidence does not match the exact learning target identity",
            details={
                "evaluation_run_id": evaluation_run_id,
                "target_type": target.resource_type.value,
                "target_id": target.resource_id,
                "target_revision": target.revision,
            },
        )


def _has_exact_target_reference(
    references: Any,
    target: LearningTarget,
    expected_ids: set[str],
) -> bool:
    expected_revision = str(target.revision)
    for reference in references:
        if reference.kind != target.resource_type.value or reference.ref_id not in expected_ids:
            continue
        if str(getattr(reference, "version", "")) == expected_revision:
            return True
    return False


def _manifest_contains_learning_reference(manifest: Any, expected: LearningReference) -> bool:
    groups = (
        manifest.configuration_references,
        manifest.skill_bundles,
        manifest.context_bundles,
        manifest.research_evidence,
        manifest.fixture_sources,
        manifest.verification_policies,
        manifest.dependencies,
        manifest.contract_versions,
        (() if manifest.workspace is None else (manifest.workspace,)),
    )
    for group in groups:
        for reference in group:
            if reference.kind != expected.kind or reference.ref_id != expected.resource_id:
                continue
            if expected.revision is not None and expected.revision not in {
                getattr(reference, "revision", None),
                getattr(reference, "version", None),
            }:
                continue
            if (
                expected.digest is not None
                and getattr(reference, "digest", None) != expected.digest
            ):
                continue
            return True
    return False


def _require_comparison_identity(run: Any, comparison: Any, evaluation_run_id: str) -> None:
    baseline_run_id = getattr(run, "baseline_run_id", None)
    if baseline_run_id is None:
        raise ContractError(
            ErrorCode.CONFLICT,
            "learning regression evidence has no persisted baseline run",
            details={"evaluation_run_id": evaluation_run_id},
        )
    if comparison.current_run_id != run.run_id or comparison.baseline_run_id != baseline_run_id:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Evaluation comparison does not match the current/baseline run references",
            details={
                "evaluation_run_id": evaluation_run_id,
                "baseline_run_id": baseline_run_id,
            },
        )


def _require_verification_target_binding(
    target: LearningTarget,
    proposed_artifact_ref: LearningReference | None,
    request: Any,
    result: Any | None,
    verification_id: str,
) -> None:
    if target.resource_type not in _IDENTITY_BOUND_TARGET_TYPES:
        return
    subject = request.subject
    if result is not None and result.subject != subject:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Verification result does not match the exact requested subject",
            details={"verification_id": verification_id},
        )
    if proposed_artifact_ref is not None and _verification_subject_matches_reference(
        subject, proposed_artifact_ref
    ):
        return
    producer = getattr(request, "producer", None)
    if (
        target.resource_type is LearningTargetType.AGENT
        and producer is not None
        and getattr(producer, "agent_id", None) == target.resource_id
        and getattr(producer, "agent_revision", None) == target.revision
    ):
        return
    raise ContractError(
        ErrorCode.CONTRACT_VIOLATION,
        "Verification evidence is not bound to the exact learning target or candidate artifact",
        details={
            "verification_id": verification_id,
            "target_type": target.resource_type.value,
            "target_id": target.resource_id,
            "target_revision": target.revision,
        },
    )


def _verification_subject_matches_reference(subject: Any, reference: LearningReference) -> bool:
    if subject.subject_type != reference.kind or subject.subject_id != reference.resource_id:
        return False
    if reference.revision is not None and subject.revision != reference.revision:
        return False
    if reference.digest is not None and subject.digest != reference.digest:
        return False
    return reference.revision is not None or reference.digest is not None
