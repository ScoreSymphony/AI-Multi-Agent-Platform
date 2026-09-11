"""Completion hardening for canonical application release gates (#750)."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.verification import VerificationOutcome

from .gates import (
    ApplicationReleaseGateCoordinator as _BaseApplicationReleaseGateCoordinator,
)
from .gates import (
    DeterministicGateCheck,
    ReleaseGateRequirement,
    verification_subject,
)
from .manifest import MANIFEST_SCHEMA_VERSION, manifest_sha256, manifest_validation_errors
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


class ApplicationReleaseGateCoordinator(_BaseApplicationReleaseGateCoordinator):
    """Release-gate coordinator with restart-safe #86 recovery and manifest validation.

    The canonical Verification and Evaluation services remain authoritative. This subclass only
    improves projection/reconciliation and deterministic manifest evidence; it does not introduce
    another verification, evaluation, or execution state machine.
    """

    async def _deterministic(
        self,
        release: ApplicationRelease,
        requirement: ReleaseGateRequirement,
    ) -> GateEvidence:
        if requirement.deterministic_check is not DeterministicGateCheck.MANIFEST_CHECKSUM:
            return await super()._deterministic(release, requirement)

        excluded = (requirement.name,)
        validation_errors = manifest_validation_errors(
            release,
            exclude_gate_names=excluded,
        )
        if validation_errors:
            return GateEvidence(
                name=requirement.name,
                status=GateStatus.FAILED,
                details={
                    "gate_kind": requirement.kind.value,
                    "source_classification": requirement.kind.value,
                    "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
                    "blocking_reason": "canonical release manifest schema validation failed",
                    "validation_errors": list(validation_errors),
                },
            )

        digest = manifest_sha256(release, exclude_gate_names=excluded)
        return GateEvidence(
            name=requirement.name,
            status=GateStatus.PASSED,
            evidence_refs=(f"manifest-sha256:{digest}",),
            details={
                "gate_kind": requirement.kind.value,
                "source_classification": requirement.kind.value,
                "manifest_sha256": digest,
                "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
                "checksum_scope": "canonical-manifest-excluding-self-gate",
            },
        )

    def _verification(
        self,
        release: ApplicationRelease,
        requirement: ReleaseGateRequirement,
        existing: GateEvidence | None,
    ) -> GateEvidence:
        artifact = _artifact_for_target(release, requirement.target_id)
        if artifact is None or self.verification is None:
            return super()._verification(release, requirement, existing)

        existing_id = _detail_string(existing, "verification_id")
        if existing_id is not None:
            return super()._verification(release, requirement, existing)

        subject = verification_subject(release, artifact)
        matches = [
            (request, result)
            for request, result in self.verification.history(task_id=artifact.build_task_id)
            if request.policy_id == requirement.verification_policy_id
            and request.policy_version == requirement.verification_policy_version
            and request.stage_id == requirement.verification_stage_id
            and request.subject == subject
            and request.correlation_id == release.release_id
            and request.run_id == artifact.build_run_id
            and artifact.artifact_id in request.artifact_ids
            and request.project_id == release.project_id
        ]
        terminal = [(request, result) for request, result in matches if result is not None]
        terminal_states = {
            _verification_gate_status(result.outcome)
            for _request, result in terminal
            if result is not None
        }
        if len(terminal_states) > 1:
            refs: list[str] = []
            for request, result in terminal:
                refs.append(request.verification_id)
                if result is not None:
                    refs.append(result.verification_result_id)
            return GateEvidence(
                name=requirement.name,
                status=GateStatus.INCONCLUSIVE,
                evidence_refs=tuple(dict.fromkeys(refs)),
                details=_artifact_details(artifact)
                | {
                    "gate_kind": requirement.kind.value,
                    "source_classification": requirement.kind.value,
                    "blocking_reason": "conflicting exact-subject Verification evidence",
                    "verification_subject_revision": subject.revision,
                },
            )

        if matches:
            completed = [item for item in matches if item[1] is not None]
            candidates = completed or matches
            request, _result = max(
                candidates,
                key=lambda item: (item[0].created_at, item[0].verification_id),
            )
            recovered = GateEvidence(
                name=requirement.name,
                status=GateStatus.PENDING,
                evidence_refs=(request.verification_id,),
                details={"verification_id": request.verification_id},
            )
            return super()._verification(release, requirement, recovered)

        return super()._verification(release, requirement, existing)


def _verification_gate_status(outcome: VerificationOutcome) -> GateStatus:
    if outcome is VerificationOutcome.PASS:
        return GateStatus.PASSED
    if outcome is VerificationOutcome.INCONCLUSIVE:
        return GateStatus.INCONCLUSIVE
    return GateStatus.FAILED


def _artifact_for_target(
    release: ApplicationRelease,
    target_id: str | None,
) -> ApplicationArtifact | None:
    if target_id is None:
        return None
    return next((artifact for artifact in release.artifacts if artifact.target_id == target_id), None)


def _detail_string(gate: GateEvidence | None, name: str) -> str | None:
    if gate is None:
        return None
    value = gate.details.get(name)
    return value if isinstance(value, str) and value.strip() else None


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
