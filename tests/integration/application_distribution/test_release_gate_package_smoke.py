from __future__ import annotations

import asyncio
from dataclasses import replace

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
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.verification import (
    CanonicalVerificationAccess,
    DeterministicCheck,
    ReferenceDeterministicVerifier,
    VerificationPolicy,
    VerificationService,
    VerificationStage,
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
        application_id="package-smoke-app",
        display_name="Package Smoke App",
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
            test_gates=("package-smoke",),
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


def test_package_smoke_result_flows_through_canonical_verification_authority() -> None:
    async def scenario() -> None:
        release = _release()
        verification = VerificationService()
        policy = verification.register_policy(
            VerificationPolicy(
                name="application package smoke",
                stages=(
                    VerificationStage(
                        stage_id="package-smoke",
                        verifier_kind=VerifierKind.DETERMINISTIC,
                        capability_ref="application.package.smoke",
                    ),
                ),
            )
        )
        coordinator = ApplicationReleaseGateCoordinator(
            policy=StaticReleaseGatePolicy(
                (
                    ReleaseGateRequirement(
                        name="package-smoke",
                        kind=ReleaseGateKind.VERIFICATION,
                        target_id="linux-x64",
                        verification_policy_id=policy.policy_id,
                        verification_policy_version=policy.version,
                        verification_stage_id="package-smoke",
                    ),
                )
            ),
            files=_Files(),  # type: ignore[arg-type]
            verification_access=CanonicalVerificationAccess(verification),
        )

        pending = (await coordinator.reconcile(release))[0]
        verification_id = pending.details["verification_id"]
        assert isinstance(verification_id, str)
        request = verification.get_request(verification_id)
        assert request.requested_capability_ref == "application.package.smoke"

        result = verification.run_deterministic(
            verification_id,
            ReferenceDeterministicVerifier(
                "deterministic:package-smoke",
                (
                    DeterministicCheck(
                        name="package_smoke",
                        predicate=lambda current: (
                            current.subject.digest == release.artifacts[0].sha256
                        ),
                        failure_message="package smoke failed",
                    ),
                ),
            ),
        )
        assert result.checks_executed == ("package_smoke",)

        projected = (await coordinator.reconcile(replace(release, gates=(pending,))))[0]
        assert projected.status is GateStatus.PASSED
        assert projected.details["verification_id"] == verification_id
        assert projected.details["verification_result_id"] == result.verification_result_id
        assert projected.details["verifier_ref"] == "deterministic:package-smoke"

    asyncio.run(scenario())
