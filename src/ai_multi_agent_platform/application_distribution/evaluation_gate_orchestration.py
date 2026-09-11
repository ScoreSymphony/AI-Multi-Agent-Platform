"""Canonical #19 Evaluation orchestration for application release gates (#750).

Application distribution owns only the release requirement and projection. Missing configured
Evaluation evidence is obtained through the canonical EvaluationService and then read back through
the canonical EvaluationHistoryRepository; no EvaluationRun or EvaluationResult is fabricated here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.data import FileProvider
from ai_multi_agent_platform.evaluation import (
    ConfigurationSnapshot,
    EvaluationOutcome,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluationService,
    VersionReference,
)
from ai_multi_agent_platform.evaluation.contracts import EvaluationHistoryRepository
from ai_multi_agent_platform.verification import CanonicalVerificationAccess, VerificationService

from .gate_hardening import (
    ApplicationReleaseGateCoordinator as _HardenedApplicationReleaseGateCoordinator,
)
from .gates import (
    ReleaseGateKind,
    ReleaseGatePolicy,
    ReleaseGateRequirement,
    _artifact_details,
    _gate,
    artifact_subject_revision,
    required_gate_names,
)
from .models import ApplicationArtifact, ApplicationRelease, GateEvidence, GateStatus


class ApplicationReleaseGateCoordinator(_HardenedApplicationReleaseGateCoordinator):
    """Drive and project only exact-configuration #19 Evaluation evidence.

    The coordinator deliberately does not own Evaluation lifecycle state. It asks the canonical
    EvaluationService to execute an exact suite version, and projection reads only canonical
    persisted run/result evidence whose semantic ConfigurationSnapshot exactly matches the current
    release subject. Snapshot IDs are excluded because they identify a run snapshot instance rather
    than its configuration content.
    """

    def __init__(
        self,
        *,
        policy: ReleaseGatePolicy,
        files: FileProvider,
        verification_access: CanonicalVerificationAccess | None = None,
        verification: VerificationService | None = None,
        evaluations: EvaluationHistoryRepository | None = None,
        evaluation_service: EvaluationService | None = None,
    ) -> None:
        super().__init__(
            policy=policy,
            files=files,
            verification_access=verification_access,
            verification=verification,
            evaluations=evaluations,
        )
        self.evaluation_service = evaluation_service
        self._evaluation_drive_lock = asyncio.Lock()

    async def reconcile(self, release: ApplicationRelease) -> tuple[GateEvidence, ...]:
        await self._drive_missing_evaluations(release)
        return await super().reconcile(release)

    async def _drive_missing_evaluations(self, release: ApplicationRelease) -> None:
        if self.evaluation_service is None or self.evaluations is None:
            return

        # Reconciliation can be triggered concurrently by resource reads, preview and publish.
        # Serialize only the short canonical launch decision so one coordinator instance cannot
        # start duplicate exact-subject Evaluation runs for the same release.
        async with self._evaluation_drive_lock:
            for name in required_gate_names(release):
                requirement = self.policy.requirement(name)
                if requirement is None or requirement.kind is not ReleaseGateKind.EVALUATION:
                    continue
                await self._ensure_evaluation_run(release, requirement)

    async def _ensure_evaluation_run(
        self,
        release: ApplicationRelease,
        requirement: ReleaseGateRequirement,
    ) -> None:
        assert self.evaluation_service is not None
        assert self.evaluations is not None

        artifact = _artifact_for_target(release, requirement.target_id)
        suite_id = requirement.evaluation_suite_id
        suite_version = requirement.evaluation_suite_version
        if artifact is None or suite_id is None or suite_version is None:
            return
        if _exact_evaluation_runs(self.evaluations, release, requirement, artifact):
            return

        snapshot = _evaluation_snapshot(release, artifact)
        try:
            await self.evaluation_service.run_suite(
                suite_ref=f"{suite_id}@{suite_version}",
                snapshot=snapshot,
            )
        except Exception:
            # EvaluationRunner durably marks a started run FAILED before re-raising. Errors that
            # happen before a run exists (for example a missing suite/provider) remain missing
            # evidence. In both cases projection stays fail-closed instead of turning a quality
            # failure into fabricated release evidence or a Control Plane read failure.
            return

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
        expected_snapshot = _evaluation_snapshot(release, artifact)
        expected_fingerprint = evaluation_snapshot_fingerprint(expected_snapshot)
        matches = list(_exact_evaluation_runs(self.evaluations, release, requirement, artifact))
        if not matches:
            return _gate(
                requirement,
                GateStatus.PENDING,
                blocking_reason="no exact-subject/configuration Evaluation run is available",
                details=_artifact_details(artifact)
                | {
                    "evaluation_suite_id": requirement.evaluation_suite_id,
                    "evaluation_suite_version": requirement.evaluation_suite_version,
                    "evaluation_subject_revision": revision,
                    "evaluation_snapshot_fingerprint": expected_fingerprint,
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
                details=_artifact_details(artifact)
                | {
                    "evaluation_subject_revision": revision,
                    "evaluation_snapshot_fingerprint": expected_fingerprint,
                },
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
                "evaluation_snapshot_id": run.snapshot.snapshot_id,
                "evaluation_snapshot_fingerprint": expected_fingerprint,
                "checked_at": None if run.completed_at is None else run.completed_at.isoformat(),
            },
        )


def evaluation_snapshot_fingerprint(snapshot: ConfigurationSnapshot) -> str:
    """Hash semantic Evaluation configuration while ignoring the per-instance snapshot ID."""

    payload = {
        "schema_version": snapshot.schema_version,
        "platform_version": snapshot.platform_version,
        "platform_commit": snapshot.platform_commit,
        "references": [
            {
                "kind": reference.kind,
                "ref_id": reference.ref_id,
                "version": reference.version,
                "revision": reference.revision,
            }
            for reference in sorted(
                snapshot.references,
                key=lambda item: (item.kind, item.ref_id, item.version, item.revision or ""),
            )
        ],
        "environment": [
            {"key": item.key, "value": item.value}
            for item in sorted(snapshot.environment, key=lambda item: item.key)
        ],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _evaluation_snapshot(
    release: ApplicationRelease,
    artifact: ApplicationArtifact,
) -> ConfigurationSnapshot:
    return ConfigurationSnapshot(
        platform_version=__version__,
        references=(
            VersionReference(
                kind="application_release_artifact",
                ref_id=artifact.artifact_id,
                version=artifact.sha256,
                revision=artifact_subject_revision(release, artifact),
            ),
        ),
    )


def _exact_evaluation_runs(
    evaluations: EvaluationHistoryRepository,
    release: ApplicationRelease,
    requirement: ReleaseGateRequirement,
    artifact: ApplicationArtifact,
) -> tuple[EvaluationRun, ...]:
    expected_fingerprint = evaluation_snapshot_fingerprint(_evaluation_snapshot(release, artifact))
    return tuple(
        run
        for run in evaluations.list_runs(
            suite_id=requirement.evaluation_suite_id,
            suite_version=requirement.evaluation_suite_version,
            limit=100,
        )
        if evaluation_snapshot_fingerprint(run.snapshot) == expected_fingerprint
    )


def _artifact_for_target(
    release: ApplicationRelease,
    target_id: str | None,
) -> ApplicationArtifact | None:
    if target_id is None:
        return None
    return next(
        (artifact for artifact in release.artifacts if artifact.target_id == target_id),
        None,
    )
