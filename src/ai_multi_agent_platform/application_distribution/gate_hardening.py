"""Completion hardening for canonical application release gates."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import FileProvider
from ai_multi_agent_platform.evaluation.contracts import EvaluationHistoryRepository
from ai_multi_agent_platform.verification import (
    CanonicalVerificationAccess,
    VerificationOutcome,
    VerificationRequest,
    VerificationRequestStatus,
    VerificationService,
)
from ai_multi_agent_platform.verification.async_canonical_access import (
    AsyncCanonicalVerificationService,
    runtime_canonical_verification_service,
)

from .gates import (
    ApplicationReleaseGateCoordinator as _BaseApplicationReleaseGateCoordinator,
)
from .gates import (
    DeterministicGateCheck,
    ReleaseGateKind,
    ReleaseGatePolicy,
    ReleaseGateRequirement,
    _gate,
    bind_gate_to_release,
    release_subject_digest,
    required_gate_names,
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
    """Release-gate coordinator with restart-safe Verification recovery and manifest validation.

    The canonical Verification and Evaluation services remain authoritative. This subclass only
    improves projection/reconciliation and deterministic manifest evidence; it does not introduce
    another verification, evaluation, or execution state machine.
    """

    def __init__(
        self,
        *,
        policy: ReleaseGatePolicy,
        files: FileProvider,
        verification_access: CanonicalVerificationAccess | None = None,
        verification: VerificationService | None = None,
        evaluations: EvaluationHistoryRepository | None = None,
        runtime_verification: AsyncCanonicalVerificationService | None = None,
    ) -> None:
        super().__init__(
            policy=policy,
            files=files,
            verification_access=verification_access,
            verification=verification,
            evaluations=evaluations,
        )
        self._runtime_verification = (
            None
            if self.verification is None
            else runtime_canonical_verification_service(
                self.verification,
                runtime_service=runtime_verification,
            )
        )

    async def reconcile(self, release: ApplicationRelease) -> tuple[GateEvidence, ...]:
        """Project release gates without running Verification persistence on the event loop.

        Manifest checksum evidence must include the current projections of every other release
        gate. Deferring manifest checksum gates until those projections are known keeps the first
        reconciliation identical to retries/restarts instead of hashing a partially projected
        release on the first pass and a fully projected release on the second.
        """

        required_names = required_gate_names(release)
        manifest_names = tuple(
            name
            for name in required_names
            if (requirement := self.policy.requirement(name)) is not None
            and requirement.kind is ReleaseGateKind.DETERMINISTIC
            and requirement.deterministic_check is DeterministicGateCheck.MANIFEST_CHECKSUM
        )
        existing = {gate.name: gate for gate in release.gates}
        projected: dict[str, GateEvidence] = {}

        for name in required_names:
            if name in manifest_names:
                continue
            requirement = self.policy.requirement(name)
            if requirement is None:
                gate = existing.get(name)
                if gate is not None:
                    projected[name] = gate
                continue
            if requirement.kind is ReleaseGateKind.DETERMINISTIC:
                gate = await self._deterministic(release, requirement)
            elif requirement.kind is ReleaseGateKind.VERIFICATION:
                gate = await self._verification_async(release, requirement, existing.get(name))
            else:
                gate = self._evaluation(release, requirement)
            projected[name] = bind_gate_to_release(gate, release)

        required = set(required_names)
        passthrough = tuple(gate for gate in release.gates if gate.name not in required)
        if not manifest_names:
            ordered = [projected[name] for name in required_names if name in projected]
            ordered.extend(passthrough)
            return tuple(ordered)

        manifest_basis = replace(
            release,
            gates=tuple(projected[name] for name in required_names if name in projected)
            + passthrough,
        )

        for name in manifest_names:
            requirement = self.policy.requirement(name)
            assert requirement is not None
            gate = await self._deterministic(manifest_basis, requirement)
            projected[name] = bind_gate_to_release(gate, release)

        ordered = [projected[name] for name in required_names if name in projected]
        ordered.extend(passthrough)
        return tuple(ordered)

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
                "manifest_subject_sha256": release_subject_digest(release),
                "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
                "checksum_scope": "canonical-manifest-excluding-self-gate",
            },
        )

    async def _verification_async(
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

        runtime = self._runtime_verification
        if runtime is None:
            return _gate(
                requirement,
                GateStatus.INCONCLUSIVE,
                blocking_reason="canonical Verification is unavailable",
                details=_artifact_details(artifact),
            )

        subject = verification_subject(release, artifact)
        verification_id = _detail_string(existing, "verification_id")
        request: VerificationRequest | None = None
        if verification_id is not None:
            try:
                candidate = await runtime.get_request(verification_id)
            except ContractError as exc:
                if exc.code is not ErrorCode.NOT_FOUND:
                    raise
                verification_id = None
            else:
                if _verification_request_matches(
                    candidate,
                    release,
                    requirement,
                    artifact,
                    subject,
                ):
                    request = candidate
                else:
                    verification_id = None

        if request is None:
            matches = [
                (candidate, result)
                for candidate, result in await runtime.history(task_id=artifact.build_task_id)
                if _verification_request_matches(
                    candidate,
                    release,
                    requirement,
                    artifact,
                    subject,
                )
            ]
            terminal = [(candidate, result) for candidate, result in matches if result is not None]
            terminal_states = {
                _verification_gate_status(result.outcome)
                for _candidate, result in terminal
                if result is not None
            }
            if len(terminal_states) > 1:
                refs: list[str] = []
                for candidate, result in terminal:
                    refs.append(candidate.verification_id)
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
                recovered, _result = max(
                    candidates,
                    key=lambda item: (item[0].created_at, item[0].verification_id),
                )
                verification_id = recovered.verification_id
                request = await runtime.get_request(verification_id)
            else:
                created = await runtime.request_canonical_verification(
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

        assert request is not None
        verification_id = request.verification_id
        verification_result = await runtime.result_for(verification_id)
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
        if verification_result is None:
            return _gate(
                requirement,
                GateStatus.PENDING,
                evidence_refs=(verification_id,),
                blocking_reason="verification is pending",
                details=_artifact_details(artifact) | {"verification_id": verification_id},
            )
        if verification_result.subject != subject:
            return _gate(
                requirement,
                GateStatus.INCONCLUSIVE,
                evidence_refs=(verification_id, verification_result.verification_result_id),
                blocking_reason="verification result does not certify the current artifact",
                details=_artifact_details(artifact) | {"verification_id": verification_id},
            )
        status = _verification_gate_status(verification_result.outcome)
        return _gate(
            requirement,
            status,
            evidence_refs=(verification_id, verification_result.verification_result_id),
            blocking_reason=(
                None
                if status is GateStatus.PASSED
                else f"verification {verification_result.outcome.value}"
            ),
            details=_artifact_details(artifact)
            | {
                "verification_id": verification_id,
                "verification_result_id": verification_result.verification_result_id,
                "verifier_ref": verification_result.verifier.verifier_ref,
                "verifier_kind": verification_result.verifier.kind.value,
                "checked_at": verification_result.completed_at.isoformat(),
            },
        )


def _verification_request_matches(
    request: VerificationRequest,
    release: ApplicationRelease,
    requirement: ReleaseGateRequirement,
    artifact: ApplicationArtifact,
    subject: object,
) -> bool:
    return (
        request.task_id == artifact.build_task_id
        and request.policy_id == requirement.verification_policy_id
        and request.policy_version == requirement.verification_policy_version
        and request.stage_id == requirement.verification_stage_id
        and request.subject == subject
        and request.correlation_id == release.release_id
        and request.run_id == artifact.build_run_id
        and request.artifact_ids == (artifact.artifact_id,)
        and request.project_id == release.project_id
    )


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
    return next(
        (artifact for artifact in release.artifacts if artifact.target_id == target_id), None
    )


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
