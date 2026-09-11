"""Canonical #19 Evaluation orchestration for application release gates (#750).

Application distribution owns only the release requirement and projection. Missing configured
Evaluation evidence is obtained through the canonical EvaluationService and then read back through
the canonical EvaluationHistoryRepository; no EvaluationRun or EvaluationResult is fabricated here.
"""

from __future__ import annotations

import asyncio

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.data import FileProvider
from ai_multi_agent_platform.evaluation import (
    ConfigurationSnapshot,
    EvaluationRun,
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
    artifact_subject_revision,
    required_gate_names,
)
from .models import ApplicationArtifact, ApplicationRelease, GateEvidence


class ApplicationReleaseGateCoordinator(_HardenedApplicationReleaseGateCoordinator):
    """Drive missing configured #19 runs before projecting canonical gate evidence.

    The coordinator deliberately does not own Evaluation lifecycle state. It asks the canonical
    EvaluationService to execute an exact suite version, and the inherited projection reads only
    canonical persisted run/result evidence. Existing exact-subject runs are always reused.
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

        revision = artifact_subject_revision(release, artifact)
        snapshot = ConfigurationSnapshot(
            platform_version=__version__,
            references=(
                VersionReference(
                    kind="application_release_artifact",
                    ref_id=artifact.artifact_id,
                    version=artifact.sha256,
                    revision=revision,
                ),
            ),
        )
        try:
            await self.evaluation_service.run_suite(
                suite_ref=f"{suite_id}@{suite_version}",
                snapshot=snapshot,
            )
        except Exception:
            # EvaluationRunner durably marks a started run FAILED before re-raising. Errors that
            # happen before a run exists (for example a missing suite/provider) remain missing
            # evidence. In both cases inherited projection stays fail-closed instead of turning a
            # quality failure into fabricated release evidence or a Control Plane read failure.
            return


def _exact_evaluation_runs(
    evaluations: EvaluationHistoryRepository,
    release: ApplicationRelease,
    requirement: ReleaseGateRequirement,
    artifact: ApplicationArtifact,
) -> tuple[EvaluationRun, ...]:
    revision = artifact_subject_revision(release, artifact)
    return tuple(
        run
        for run in evaluations.list_runs(
            suite_id=requirement.evaluation_suite_id,
            suite_version=requirement.evaluation_suite_version,
            limit=100,
        )
        if any(
            reference.kind in {"application_release_artifact", "artifact"}
            and reference.ref_id == artifact.artifact_id
            and reference.version == artifact.sha256
            and reference.revision == revision
            for reference in run.snapshot.references
        )
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
