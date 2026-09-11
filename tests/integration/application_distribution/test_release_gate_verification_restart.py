from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from ai_multi_agent_platform.application_distribution import (
    ApplicationArtifact,
    ApplicationRelease,
    ApplicationReleaseGateCoordinator,
    BuildSpecification,
    BuildTarget,
    BuildTargetState,
    BuildTargetStatus,
    GateStatus,
    PackageType,
    ReleaseChannel,
    ReleaseGateKind,
    ReleaseGateRequirement,
    ReleaseStatus,
    ReleaseVisibility,
    StaticReleaseGatePolicy,
    verification_subject,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.verification import (
    CanonicalVerificationAccess,
    SqliteVerificationService,
    VerificationOutcome,
    VerificationPolicy,
    VerificationResult,
    VerificationStage,
    VerifierIdentity,
    VerifierKind,
)


class _Files:
    async def verify_checksum(self, file_id: str, context: object) -> bool:
        del file_id, context
        return True


def _release() -> ApplicationRelease:
    target = BuildTarget(
        target_id="linux-x64",
        os_name="linux",
        architecture="x86_64",
        package_type=PackageType.ARCHIVE,
        output_path="dist/app.tar.gz",
    )
    task_id = new_id("task")
    run_id = new_id("run")
    artifact = ApplicationArtifact(
        artifact_id=new_id("artifact"),
        file_id=new_id("file"),
        target_id=target.target_id,
        filename="dist/app.tar.gz",
        package_type=target.package_type,
        media_type="application/gzip",
        sha256="a" * 64,
        build_task_id=task_id,
        build_run_id=run_id,
    )
    return ApplicationRelease(
        application_id="restart-app",
        display_name="Restart App",
        version="1.0.0",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PUBLIC,
        project_id=new_id("project"),
        workspace_id=new_id("workspace"),
        workspace_snapshot_id=new_id("workspace_snapshot"),
        workspace_content_checksum="c" * 64,
        source_revision="0123456789abcdef0123456789abcdef01234567",
        build_specification=BuildSpecification(
            command=("python", "-m", "build"),
            targets=(target,),
            test_gates=("verification",),
        ),
        creator_ref="user:tester",
        status=ReleaseStatus.READY,
        targets=(
            BuildTargetState(
                target=target,
                status=BuildTargetStatus.SUCCEEDED,
                task_id=task_id,
                run_id=run_id,
            ),
        ),
        artifacts=(artifact,),
    )


def _setup(
    verification: SqliteVerificationService,
    release: ApplicationRelease,
    *,
    policy_id: str | None = None,
    policy_version: int | None = None,
) -> tuple[ApplicationReleaseGateCoordinator, VerificationPolicy]:
    if policy_id is None:
        policy = verification.register_policy(
            VerificationPolicy(
                name="release restart verification",
                stages=(
                    VerificationStage(
                        stage_id="release-review",
                        verifier_kind=VerifierKind.DETERMINISTIC,
                    ),
                ),
            )
        )
    else:
        assert policy_version is not None
        policy = verification.get_policy(policy_id, policy_version)
    coordinator = ApplicationReleaseGateCoordinator(
        policy=StaticReleaseGatePolicy(
            (
                ReleaseGateRequirement(
                    name="verification",
                    kind=ReleaseGateKind.VERIFICATION,
                    target_id=release.artifacts[0].target_id,
                    verification_policy_id=policy.policy_id,
                    verification_policy_version=policy.version,
                    verification_stage_id="release-review",
                ),
            )
        ),
        files=_Files(),  # type: ignore[arg-type]
        verification_access=CanonicalVerificationAccess(verification),
    )
    return coordinator, policy


def _submit(
    verification: SqliteVerificationService,
    verification_id: str,
    outcome: VerificationOutcome,
) -> None:
    request = verification.get_request(verification_id)
    verification.submit_result(
        VerificationResult(
            verification_id=verification_id,
            verifier=VerifierIdentity(
                verifier_ref="deterministic:restart-check",
                kind=VerifierKind.DETERMINISTIC,
                read_only=True,
            ),
            outcome=outcome,
            subject=request.subject,
            checks_executed=("package_smoke",),
        )
    )


def test_completed_verification_is_reconstructed_after_real_sqlite_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        release = _release()
        database = tmp_path / "verification.sqlite3"
        first = SqliteVerificationService(database)
        coordinator, policy = _setup(first, release)
        pending = (await coordinator.reconcile(release))[0]
        verification_id = pending.details["verification_id"]
        assert isinstance(verification_id, str)
        _submit(first, verification_id, VerificationOutcome.PASS)

        restarted = SqliteVerificationService(database)
        recovered_coordinator, _ = _setup(
            restarted,
            release,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        )
        recovered = (await recovered_coordinator.reconcile(replace(release, gates=())))[0]

        assert recovered.status is GateStatus.PASSED
        assert recovered.details["verification_id"] == verification_id
        assert recovered.details["verification_result_id"] == (
            restarted.result_for(verification_id).verification_result_id  # type: ignore[union-attr]
        )
        assert len(restarted.history(task_id=release.artifacts[0].build_task_id)) == 1

    asyncio.run(scenario())


def test_conflicting_exact_subject_verification_history_is_explicitly_inconclusive(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        release = _release()
        verification = SqliteVerificationService(tmp_path / "verification.sqlite3")
        coordinator, policy = _setup(verification, release)
        first_gate = (await coordinator.reconcile(release))[0]
        first_id = first_gate.details["verification_id"]
        assert isinstance(first_id, str)
        _submit(verification, first_id, VerificationOutcome.PASS)

        artifact = release.artifacts[0]
        access = CanonicalVerificationAccess(verification)
        duplicate = access.request_verification(
            task_id=artifact.build_task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="release-review",
            subject=verification_subject(release, artifact),
            correlation_id=release.release_id,
            run_id=artifact.build_run_id,
            artifact_ids=(artifact.artifact_id,),
            project_id=release.project_id,
        )
        _submit(verification, duplicate.verification_id, VerificationOutcome.FAIL)

        conflicted = (await coordinator.reconcile(replace(release, gates=())))[0]
        assert conflicted.status is GateStatus.INCONCLUSIVE
        assert (
            "conflicting exact-subject Verification evidence"
            == conflicted.details["blocking_reason"]
        )
        assert first_id in conflicted.evidence_refs
        assert duplicate.verification_id in conflicted.evidence_refs

    asyncio.run(scenario())
