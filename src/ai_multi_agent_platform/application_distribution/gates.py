"""Canonical release-gate projection for application distribution.

This module coordinates existing Verification (#86) and Evaluation (#19) authorities
instead of creating a second verification/evaluation lifecycle. Application distribution
owns only the named release requirement, its projection, and the publication decision.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.evaluation import (
    EvaluationOutcome,
    EvaluationRun,
    EvaluationRunStatus,
)
from ai_multi_agent_platform.evaluation.contracts import EvaluationHistoryRepository
from ai_multi_agent_platform.security import infer_actor_identity
from ai_multi_agent_platform.verification import (
    CanonicalVerificationAccess,
    VerificationOutcome,
    VerificationRequestStatus,
    VerificationService,
    VerificationSubject,
)

from .models import ApplicationArtifact, ApplicationRelease, GateEvidence, GateStatus

_RUNTIME_METADATA_KEYS = frozenset(
    {
        "worker_id",
        "node_id",
        "executor_id",
        "executor_version",
        "runtime_id",
        "runtime_version",
        "os",
        "architecture",
        "tool_versions",
        "build_provider_version",
        "environment_fingerprint",
    }
)


class ReleaseGateKind(StrEnum):
    DETERMINISTIC = "deterministic"
    VERIFICATION = "verification"
    EVALUATION = "evaluation"


class DeterministicGateCheck(StrEnum):
    ARTIFACT_EXISTS = "artifact_exists"
    FILE_CHECKSUM = "file_checksum"
    MANIFEST_CHECKSUM = "manifest_checksum"


@dataclass(frozen=True, slots=True)
class ReleaseGateRequirement:
    """Policy-owned mapping from a BuildSpecification gate name to canonical evidence."""

    name: str
    kind: ReleaseGateKind
    target_id: str | None = None
    deterministic_check: DeterministicGateCheck | None = None
    verification_policy_id: str | None = None
    verification_policy_version: int | None = None
    verification_stage_id: str | None = None
    evaluation_suite_id: str | None = None
    evaluation_suite_version: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("release gate requirement name must not be blank")
        if self.target_id is not None and not self.target_id.strip():
            raise ValueError("release gate target_id must not be blank")
        if self.kind is ReleaseGateKind.DETERMINISTIC:
            if self.deterministic_check is None:
                raise ValueError("deterministic release gates require deterministic_check")
            if (
                self.deterministic_check is not DeterministicGateCheck.MANIFEST_CHECKSUM
                and self.target_id is None
            ):
                raise ValueError("artifact/file deterministic gates require target_id")
        elif self.kind is ReleaseGateKind.VERIFICATION:
            if self.target_id is None:
                raise ValueError("verification release gates require target_id")
            if self.verification_policy_id is None or not self.verification_policy_id.strip():
                raise ValueError("verification release gate requires policy id")
            if self.verification_policy_version is None or self.verification_policy_version < 1:
                raise ValueError("verification release gate requires policy version >= 1")
            if self.verification_stage_id is None or not self.verification_stage_id.strip():
                raise ValueError("verification release gate requires stage id")
        elif self.kind is ReleaseGateKind.EVALUATION:
            if self.target_id is None:
                raise ValueError("evaluation release gates require target_id")
            if self.evaluation_suite_id is None or not self.evaluation_suite_id.strip():
                raise ValueError("evaluation release gate requires suite id")
            if self.evaluation_suite_version is None or not self.evaluation_suite_version.strip():
                raise ValueError("evaluation release gate requires suite version")


class ReleaseGatePolicy(Protocol):
    def requirement(self, name: str) -> ReleaseGateRequirement | None: ...


class StaticReleaseGatePolicy:
    """Small reference policy registry; production composition may source this elsewhere."""

    def __init__(self, requirements: tuple[ReleaseGateRequirement, ...] = ()) -> None:
        by_name = {item.name: item for item in requirements}
        if len(by_name) != len(requirements):
            raise ValueError("release gate requirement names must be unique")
        self._requirements = by_name

    def requirement(self, name: str) -> ReleaseGateRequirement | None:
        return self._requirements.get(name)


class ApplicationReleaseGateCoordinator:
    """Project canonical evidence into the application-release gate view."""

    def __init__(
        self,
        *,
        policy: ReleaseGatePolicy,
        files: FileProvider,
        verification_access: CanonicalVerificationAccess | None = None,
        verification: VerificationService | None = None,
        evaluations: EvaluationHistoryRepository | None = None,
    ) -> None:
        self.policy = policy
        self.files = files
        self.verification_access = verification_access
        self.verification = verification or (
            None if verification_access is None else verification_access.verification
        )
        self.evaluations = evaluations

    async def reconcile(self, release: ApplicationRelease) -> tuple[GateEvidence, ...]:
        existing = {gate.name: gate for gate in release.gates}
        projected: list[GateEvidence] = []
        for name in required_gate_names(release):
            requirement = self.policy.requirement(name)
            if requirement is None:
                gate = existing.get(name)
                if gate is not None:
                    projected.append(bind_gate_to_release(gate, release))
                continue
            if requirement.kind is ReleaseGateKind.DETERMINISTIC:
                gate = await self._deterministic(release, requirement)
            elif requirement.kind is ReleaseGateKind.VERIFICATION:
                gate = self._verification(release, requirement, existing.get(name))
            else:
                gate = self._evaluation(release, requirement)
            projected.append(bind_gate_to_release(gate, release))

        required = set(required_gate_names(release))
        projected.extend(gate for gate in release.gates if gate.name not in required)
        return tuple(projected)

    async def _deterministic(
        self,
        release: ApplicationRelease,
        requirement: ReleaseGateRequirement,
    ) -> GateEvidence:
        check = requirement.deterministic_check
        if check is DeterministicGateCheck.MANIFEST_CHECKSUM:
            # The published manifest contains gate evidence itself. Hashing the full manifest
            # into one of its own gates would be self-referential, so this deterministic gate
            # binds the gate-independent release subject that the manifest represents.
            digest = release_subject_digest(release)
            return _gate(
                requirement,
                GateStatus.PASSED,
                evidence_refs=(f"release-subject-sha256:{digest}",),
                details={"manifest_subject_sha256": digest},
            )

        artifact = _artifact_for_target(release, requirement.target_id)
        if artifact is None:
            return _gate(
                requirement,
                GateStatus.FAILED,
                blocking_reason="target artifact is missing",
            )
        if check is DeterministicGateCheck.ARTIFACT_EXISTS:
            return _gate(
                requirement,
                GateStatus.PASSED,
                evidence_refs=(artifact.artifact_id, artifact.file_id),
                details=_artifact_details(artifact),
            )
        if check is DeterministicGateCheck.FILE_CHECKSUM:
            ok = await self.files.verify_checksum(
                artifact.file_id,
                _data_context(release, artifact),
            )
            return _gate(
                requirement,
                GateStatus.PASSED if ok else GateStatus.FAILED,
                evidence_refs=(artifact.artifact_id, artifact.file_id),
                blocking_reason=None if ok else "canonical File checksum verification failed",
                details=_artifact_details(artifact),
            )
        return _gate(
            requirement,
            GateStatus.INCONCLUSIVE,
            blocking_reason="unsupported deterministic release check",
        )

    def _verification(
        self,
        release: ApplicationRelease,
        requirement: ReleaseGateRequirement,
        existing: GateEvidence | None,
    ) -> GateEvidence:
        artifact = _artifact_for_target(release, requirement.target_id)
        if artifact is None:
            return _gate(
                requirement,
                GateStatus.PENDING,
                blocking_reason="target artifact is not available for verification",
            )
        if self.verification_access is None or self.verification is None:
            return _gate(
                requirement,
                GateStatus.INCONCLUSIVE,
                blocking_reason="canonical Verification is unavailable",
                details=_artifact_details(artifact),
            )

        subject = verification_subject(release, artifact)
        verification_id = _detail_string(existing, "verification_id")
        if verification_id is not None:
            try:
                existing_request = self.verification.get_request(verification_id)
            except ContractError as exc:
                if exc.code is not ErrorCode.NOT_FOUND:
                    raise
                verification_id = None
            else:
                if existing_request.subject != subject:
                    verification_id = None

        if verification_id is None:
            created = self.verification_access.request_verification(
                task_id=artifact.build_task_id,
                policy_id=requirement.verification_policy_id or "",
                policy_version=requirement.verification_policy_version or 0,
                stage_id=requirement.verification_stage_id or "",
                subject=subject,
                correlation_id=release.release_id,
                run_id=artifact.build_run_id,
                artifact_ids=(artifact.artifact_id,),
                project_id=release.project_id,
            )
            return _gate(
                requirement,
                GateStatus.PENDING,
                evidence_refs=(created.verification_id,),
                blocking_reason="verification is pending",
                details=_artifact_details(artifact)
                | {
                    "verification_id": created.verification_id,
                    "verification_policy_id": created.policy_id,
                    "verification_policy_version": created.policy_version,
                    "verification_stage_id": created.stage_id,
                    "verification_subject_revision": subject.revision,
                },
            )

        request = self.verification.get_request(verification_id)
        result = self.verification.result_for(verification_id)
        if request.subject != subject:
            return _gate(
                requirement,
                GateStatus.PENDING,
                evidence_refs=(verification_id,),
                blocking_reason="verification evidence is stale for the current artifact",
                details=_artifact_details(artifact) | {"verification_id": verification_id},
            )
        if request.status in {
            VerificationRequestStatus.EXPIRED,
            VerificationRequestStatus.CANCELLED,
        }:
            return _gate(
                requirement,
                GateStatus.INCONCLUSIVE,
                evidence_refs=(verification_id,),
                blocking_reason=f"verification request is {request.status.value}",
                details=_artifact_details(artifact) | {"verification_id": verification_id},
            )
        if result is None:
            return _gate(
                requirement,
                GateStatus.PENDING,
                evidence_refs=(verification_id,),
                blocking_reason="verification is pending",
                details=_artifact_details(artifact) | {"verification_id": verification_id},
            )
        if result.subject != subject:
            return _gate(
                requirement,
                GateStatus.INCONCLUSIVE,
                evidence_refs=(verification_id, result.verification_result_id),
                blocking_reason="verification result does not certify the current artifact",
                details=_artifact_details(artifact) | {"verification_id": verification_id},
            )
        status = {
            VerificationOutcome.PASS: GateStatus.PASSED,
            VerificationOutcome.INCONCLUSIVE: GateStatus.INCONCLUSIVE,
            VerificationOutcome.FAIL: GateStatus.FAILED,
            VerificationOutcome.NEEDS_CHANGES: GateStatus.FAILED,
        }[result.outcome]
        return _gate(
            requirement,
            status,
            evidence_refs=(verification_id, result.verification_result_id),
            blocking_reason=(
                None if status is GateStatus.PASSED else f"verification {result.outcome.value}"
            ),
            details=_artifact_details(artifact)
            | {
                "verification_id": verification_id,
                "verification_result_id": result.verification_result_id,
                "verifier_ref": result.verifier.verifier_ref,
                "verifier_kind": result.verifier.kind.value,
                "checked_at": result.completed_at.isoformat(),
            },
        )

    def _evaluation(
        self,
        release: ApplicationRelease,
        requirement: ReleaseGateRequirement,
    ) -> GateEvidence:
        artifact = _artifact_for_target(release, requirement.target_id)
        if artifact is None:
            return _gate(
                requirement,
                GateStatus.PENDING,
                blocking_reason="target artifact is not available for evaluation",
            )
        if self.evaluations is None:
            return _gate(
                requirement,
                GateStatus.INCONCLUSIVE,
                blocking_reason="canonical Evaluation is unavailable",
                details=_artifact_details(artifact),
            )
        revision = artifact_subject_revision(release, artifact)
        matches: list[EvaluationRun] = []
        for run in self.evaluations.list_runs(
            suite_id=requirement.evaluation_suite_id,
            suite_version=requirement.evaluation_suite_version,
            limit=100,
        ):
            if any(
                reference.kind in {"application_release_artifact", "artifact"}
                and reference.ref_id == artifact.artifact_id
                and reference.version == artifact.sha256
                and reference.revision == revision
                for reference in run.snapshot.references
            ):
                matches.append(run)
        if not matches:
            return _gate(
                requirement,
                GateStatus.PENDING,
                blocking_reason="no exact-subject Evaluation run is available",
                details=_artifact_details(artifact)
                | {
                    "evaluation_suite_id": requirement.evaluation_suite_id,
                    "evaluation_suite_version": requirement.evaluation_suite_version,
                },
            )
        matches.sort(key=lambda item: item.started_at, reverse=True)
        terminal = [
            item
            for item in matches
            if item.status in {EvaluationRunStatus.COMPLETED, EvaluationRunStatus.FAILED}
        ]
        terminal_states: set[GateStatus] = set()
        for run in terminal:
            results = self.evaluations.list_results(run.run_id)
            if run.status is EvaluationRunStatus.FAILED or any(
                result.outcome in {EvaluationOutcome.FAILED, EvaluationOutcome.ERROR}
                for result in results
            ):
                terminal_states.add(GateStatus.FAILED)
            elif results and all(result.outcome is EvaluationOutcome.PASSED for result in results):
                terminal_states.add(GateStatus.PASSED)
            else:
                terminal_states.add(GateStatus.INCONCLUSIVE)
        if len(terminal_states) > 1:
            return _gate(
                requirement,
                GateStatus.INCONCLUSIVE,
                evidence_refs=tuple(run.run_id for run in terminal),
                blocking_reason="conflicting exact-subject Evaluation evidence",
                details=_artifact_details(artifact),
            )

        run = matches[0]
        results = self.evaluations.list_results(run.run_id)
        if run.status in {EvaluationRunStatus.PENDING, EvaluationRunStatus.RUNNING}:
            status = GateStatus.PENDING
            reason = "evaluation is pending"
        elif run.status is EvaluationRunStatus.FAILED or any(
            result.outcome in {EvaluationOutcome.FAILED, EvaluationOutcome.ERROR}
            for result in results
        ):
            status = GateStatus.FAILED
            reason = "evaluation failed"
        elif results and all(result.outcome is EvaluationOutcome.PASSED for result in results):
            status = GateStatus.PASSED
            reason = None
        else:
            status = GateStatus.INCONCLUSIVE
            reason = "evaluation completed without conclusive passing evidence"
        return _gate(
            requirement,
            status,
            evidence_refs=(run.run_id, *(result.result_id for result in results)),
            blocking_reason=reason,
            details=_artifact_details(artifact)
            | {
                "evaluation_run_id": run.run_id,
                "evaluation_suite_id": run.suite_id,
                "evaluation_suite_version": run.suite_version,
                "evaluation_subject_revision": revision,
                "checked_at": None if run.completed_at is None else run.completed_at.isoformat(),
            },
        )


def required_gate_names(release: ApplicationRelease) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                *release.build_specification.pre_build_checks,
                *release.build_specification.test_gates,
                *release.build_specification.post_build_checks,
            )
        )
    )


def release_subject_digest(release: ApplicationRelease) -> str:
    """Fingerprint only release inputs that can invalidate release-quality evidence."""

    payload = {
        "release_id": release.release_id,
        "source_revision": release.source_revision,
        "workspace_snapshot_id": release.workspace_snapshot_id,
        "workspace_content_checksum": release.workspace_content_checksum,
        "build_specification": {
            "spec_id": release.build_specification.spec_id,
            "revision": release.build_specification.revision,
        },
        "artifacts": [
            {
                "target_id": artifact.target_id,
                "artifact_id": artifact.artifact_id,
                "file_id": artifact.file_id,
                "sha256": artifact.sha256,
            }
            for artifact in sorted(release.artifacts, key=lambda item: item.target_id)
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def bind_gate_to_release(gate: GateEvidence, release: ApplicationRelease) -> GateEvidence:
    details = dict(gate.details)
    details.update(
        {
            "release_subject_digest": release_subject_digest(release),
            "source_revision": release.source_revision,
            "build_specification_id": release.build_specification.spec_id,
            "build_specification_revision": release.build_specification.revision,
        }
    )
    return replace(gate, details=details)


def gate_is_current(gate: GateEvidence, release: ApplicationRelease) -> bool:
    return gate.details.get("release_subject_digest") == release_subject_digest(release)


def publication_readiness(release: ApplicationRelease) -> dict[str, JsonValue]:
    required = required_gate_names(release)
    gates = {gate.name: gate for gate in release.gates}
    gate_states: list[JsonValue] = []
    blocking: list[JsonValue] = []
    for name in required:
        gate = gates.get(name)
        current = gate is not None and gate_is_current(gate, release)
        status = "missing" if gate is None else gate.status.value
        reason: str | None = None
        if gate is None:
            reason = "required gate evidence is missing"
        elif not current:
            reason = "gate evidence is stale for the current release subject"
        elif gate.status is not GateStatus.PASSED:
            raw = gate.details.get("blocking_reason")
            reason = raw if isinstance(raw, str) else f"gate is {gate.status.value}"
        state: dict[str, JsonValue] = {
            "name": name,
            "status": status,
            "current_subject": current,
            "blocking_reason": reason,
        }
        if gate is not None:
            state["evidence_refs"] = list(gate.evidence_refs)
            state["details"] = dict(gate.details)
        gate_states.append(state)
        if reason is not None:
            blocking.append({"gate": name, "reason": reason})
    return {
        "publication_permitted": not blocking,
        "required_gates": gate_states,
        "blocking_reasons": blocking,
        "release_subject_digest": release_subject_digest(release),
    }


def verification_subject(
    release: ApplicationRelease,
    artifact: ApplicationArtifact,
) -> VerificationSubject:
    return VerificationSubject(
        subject_type="artifact",
        subject_id=artifact.artifact_id,
        revision=artifact_subject_revision(release, artifact),
        digest=artifact.sha256,
    )


def artifact_subject_revision(release: ApplicationRelease, artifact: ApplicationArtifact) -> str:
    return (
        f"source={release.source_revision};"
        f"build={release.build_specification.spec_id}@{release.build_specification.revision};"
        f"target={artifact.target_id};file={artifact.file_id}"
    )


def _artifact_for_target(
    release: ApplicationRelease,
    target_id: str | None,
) -> ApplicationArtifact | None:
    if target_id is None:
        return None
    for artifact in release.artifacts:
        if artifact.target_id == target_id:
            return artifact
    return None


def _artifact_details(artifact: ApplicationArtifact) -> dict[str, JsonValue]:
    details: dict[str, JsonValue] = {
        "target_id": artifact.target_id,
        "artifact_id": artifact.artifact_id,
        "file_id": artifact.file_id,
        "artifact_sha256": artifact.sha256,
        "build_task_id": artifact.build_task_id,
        "build_run_id": artifact.build_run_id,
    }
    runtime = {
        key: value
        for key, value in artifact.external_metadata.items()
        if key in _RUNTIME_METADATA_KEYS
    }
    if runtime:
        details["runtime_provenance"] = runtime
    return details


def _gate(
    requirement: ReleaseGateRequirement,
    status: GateStatus,
    *,
    evidence_refs: tuple[str, ...] = (),
    blocking_reason: str | None = None,
    details: Mapping[str, JsonValue] | None = None,
) -> GateEvidence:
    payload = dict(details or {})
    payload.update(
        {
            "gate_kind": requirement.kind.value,
            "source_classification": requirement.kind.value,
        }
    )
    if blocking_reason is not None:
        payload["blocking_reason"] = blocking_reason
    return GateEvidence(
        name=requirement.name,
        status=status,
        evidence_refs=evidence_refs,
        details=payload,
    )


def _detail_string(gate: GateEvidence | None, name: str) -> str | None:
    if gate is None:
        return None
    value = gate.details.get(name)
    return value if isinstance(value, str) and value.strip() else None


def _data_context(
    release: ApplicationRelease,
    artifact: ApplicationArtifact,
) -> DataAccessContext:
    actor = infer_actor_identity(release.creator_ref)
    return DataAccessContext(
        operation=OperationContext(
            correlation_id=release.release_id,
            owner_type="user" if actor.actor_type.value == "human" else "service",
            owner_id=actor.actor_id,
            project_id=release.project_id,
        ),
        actor_ref=release.creator_ref,
        task_id=artifact.build_task_id,
        run_id=artifact.build_run_id,
        audit_metadata={"source": "application-release-gates"},
    )
