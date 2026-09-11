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
    GateEvidence,
    GateStatus,
    PackageType,
    ReleaseChannel,
    ReleaseGateKind,
    ReleaseGateRequirement,
    ReleaseStatus,
    ReleaseVisibility,
    StaticReleaseGatePolicy,
    bind_gate_to_release,
    verification_subject,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.verification import (
    CanonicalVerificationAccess,
    VerificationOutcome,
    VerificationPolicy,
    VerificationResult,
    VerificationService,
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
        application_id="verification-binding-app",
        display_name="Verification Binding App",
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


def _policy(
    verification: VerificationService,
    *,
    name: str,
    stage_id: str,
) -> VerificationPolicy:
    return verification.register_policy(
        VerificationPolicy(
            name=name,
            stages=(
                VerificationStage(
                    stage_id=stage_id,
                    verifier_kind=VerifierKind.DETERMINISTIC,
                ),
            ),
        )
    )


def _submit_pass(verification: VerificationService, verification_id: str) -> None:
    request = verification.get_request(verification_id)
    verification.submit_result(
        VerificationResult(
            verification_id=verification_id,
            verifier=VerifierIdentity(
                verifier_ref="deterministic:binding-test",
                kind=VerifierKind.DETERMINISTIC,
                read_only=True,
            ),
            outcome=VerificationOutcome.PASS,
            subject=request.subject,
            checks_executed=("binding_test",),
        )
    )


def test_same_subject_result_from_wrong_policy_cannot_satisfy_gate() -> None:
    async def scenario() -> None:
        release = _release()
        artifact = release.artifacts[0]
        verification = VerificationService()
        required_policy = _policy(
            verification,
            name="required release policy",
            stage_id="required-stage",
        )
        wrong_policy = _policy(
            verification,
            name="different release policy",
            stage_id="different-stage",
        )
        access = CanonicalVerificationAccess(verification)
        wrong = access.request_verification(
            task_id=artifact.build_task_id,
            policy_id=wrong_policy.policy_id,
            policy_version=wrong_policy.version,
            stage_id="different-stage",
            subject=verification_subject(release, artifact),
            correlation_id=release.release_id,
            run_id=artifact.build_run_id,
            artifact_ids=(artifact.artifact_id,),
            project_id=release.project_id,
        )
        _submit_pass(verification, wrong.verification_id)
        stale_projection = bind_gate_to_release(
            GateEvidence(
                name="verification",
                status=GateStatus.PASSED,
                evidence_refs=(wrong.verification_id,),
                details={"verification_id": wrong.verification_id},
            ),
            release,
        )
        coordinator = ApplicationReleaseGateCoordinator(
            policy=StaticReleaseGatePolicy(
                (
                    ReleaseGateRequirement(
                        name="verification",
                        kind=ReleaseGateKind.VERIFICATION,
                        target_id=artifact.target_id,
                        verification_policy_id=required_policy.policy_id,
                        verification_policy_version=required_policy.version,
                        verification_stage_id="required-stage",
                    ),
                )
            ),
            files=_Files(),  # type: ignore[arg-type]
            verification_access=access,
        )

        projected = (await coordinator.reconcile(replace(release, gates=(stale_projection,))))[0]
        projected_id = projected.details["verification_id"]

        assert projected.status is GateStatus.PENDING
        assert isinstance(projected_id, str)
        assert projected_id != wrong.verification_id
        exact = verification.get_request(projected_id)
        assert exact.policy_id == required_policy.policy_id
        assert exact.policy_version == required_policy.version
        assert exact.stage_id == "required-stage"
        assert exact.subject == verification_subject(release, artifact)
        assert exact.run_id == artifact.build_run_id
        assert exact.artifact_ids == (artifact.artifact_id,)
        assert exact.project_id == release.project_id

    asyncio.run(scenario())


def test_exact_requirement_request_is_reused_idempotently() -> None:
    async def scenario() -> None:
        release = _release()
        artifact = release.artifacts[0]
        verification = VerificationService()
        policy = _policy(
            verification,
            name="required release policy",
            stage_id="required-stage",
        )
        access = CanonicalVerificationAccess(verification)
        coordinator = ApplicationReleaseGateCoordinator(
            policy=StaticReleaseGatePolicy(
                (
                    ReleaseGateRequirement(
                        name="verification",
                        kind=ReleaseGateKind.VERIFICATION,
                        target_id=artifact.target_id,
                        verification_policy_id=policy.policy_id,
                        verification_policy_version=policy.version,
                        verification_stage_id="required-stage",
                    ),
                )
            ),
            files=_Files(),  # type: ignore[arg-type]
            verification_access=access,
        )

        pending = (await coordinator.reconcile(release))[0]
        verification_id = pending.details["verification_id"]
        assert isinstance(verification_id, str)
        _submit_pass(verification, verification_id)

        passed = (await coordinator.reconcile(replace(release, gates=(pending,))))[0]
        again = (await coordinator.reconcile(replace(release, gates=(passed,))))[0]

        assert passed.status is GateStatus.PASSED
        assert again == passed
        assert len(verification.history(task_id=artifact.build_task_id)) == 1

    asyncio.run(scenario())
