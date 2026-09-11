from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.application_distribution import (
    ApplicationArtifact,
    ApplicationDistributionService,
    ApplicationRelease,
    ApplicationReleaseGateCoordinator,
    BuildSpecification,
    BuildTarget,
    BuildTargetState,
    BuildTargetStatus,
    DeterministicGateCheck,
    GateStatus,
    PackageType,
    ReleaseChannel,
    ReleaseGateKind,
    ReleaseGateRequirement,
    ReleaseStatus,
    ReleaseVisibility,
    StaticReleaseGatePolicy,
    artifact_subject_revision,
    manifest_sha256,
    manifest_validation_errors,
    publication_readiness,
)
from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.evaluation import (
    ConfigurationSnapshot,
    EvaluationOutcome,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluatorDescriptor,
    EvaluatorKind,
    InMemoryEvaluationRepository,
    VersionReference,
)
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
    def __init__(self, *, valid: bool = True) -> None:
        self.valid = valid

    async def verify_checksum(self, file_id: str, context: object) -> bool:
        del file_id, context
        return self.valid


def _release(
    *gate_names: str,
    sha256: str = "a" * 64,
    source_revision: str = "0123456789abcdef0123456789abcdef01234567",
    spec_revision: int = 1,
) -> ApplicationRelease:
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
        sha256=sha256,
        build_task_id=task_id,
        build_run_id=run_id,
    )
    specification = BuildSpecification(
        command=("python", "-m", "build"),
        targets=(target,),
        spec_id=new_id("build_spec"),
        revision=spec_revision,
        test_gates=tuple(gate_names),
    )
    return ApplicationRelease(
        application_id="hardening-app",
        display_name="Hardening App",
        version="1.0.0",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PUBLIC,
        project_id=new_id("project"),
        workspace_id=new_id("workspace"),
        workspace_snapshot_id=new_id("workspace_snapshot"),
        workspace_content_checksum="c" * 64,
        source_revision=source_revision,
        build_specification=specification,
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


def _verification_coordinator(
    release: ApplicationRelease,
) -> tuple[ApplicationReleaseGateCoordinator, VerificationService]:
    verification = VerificationService()
    policy = verification.register_policy(
        VerificationPolicy(
            name="release hardening",
            stages=(
                VerificationStage(
                    stage_id="release-review",
                    verifier_kind=VerifierKind.DETERMINISTIC,
                ),
            ),
        )
    )
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
    return coordinator, verification


def _submit_verification(
    verification: VerificationService,
    verification_id: str,
    outcome: VerificationOutcome,
) -> None:
    request = verification.get_request(verification_id)
    verification.submit_result(
        VerificationResult(
            verification_id=verification_id,
            verifier=VerifierIdentity(
                verifier_ref="deterministic:release-hardening",
                kind=VerifierKind.DETERMINISTIC,
                read_only=True,
            ),
            outcome=outcome,
            subject=request.subject,
            checks_executed=("release_hardening",),
        )
    )


def test_manifest_gate_validates_schema_and_emits_stable_real_manifest_checksum() -> None:
    async def scenario() -> None:
        release = _release("manifest")
        coordinator = ApplicationReleaseGateCoordinator(
            policy=StaticReleaseGatePolicy(
                (
                    ReleaseGateRequirement(
                        name="manifest",
                        kind=ReleaseGateKind.DETERMINISTIC,
                        deterministic_check=DeterministicGateCheck.MANIFEST_CHECKSUM,
                    ),
                )
            ),
            files=_Files(),  # type: ignore[arg-type]
        )

        first = (await coordinator.reconcile(release))[0]
        reconciled = replace(release, gates=(first,))
        second = (await coordinator.reconcile(reconciled))[0]
        expected = manifest_sha256(reconciled, exclude_gate_names=("manifest",))

        assert manifest_validation_errors(reconciled, exclude_gate_names=("manifest",)) == ()
        assert first == second
        assert second.status is GateStatus.PASSED
        assert second.evidence_refs == (f"manifest-sha256:{expected}",)
        assert second.details["manifest_sha256"] == expected
        assert second.details["checksum_scope"] == "canonical-manifest-excluding-self-gate"

    asyncio.run(scenario())


def test_checksum_failure_is_current_evidence_and_blocks_publication() -> None:
    async def scenario() -> None:
        release = _release("checksum")
        coordinator = ApplicationReleaseGateCoordinator(
            policy=StaticReleaseGatePolicy(
                (
                    ReleaseGateRequirement(
                        name="checksum",
                        kind=ReleaseGateKind.DETERMINISTIC,
                        target_id="linux-x64",
                        deterministic_check=DeterministicGateCheck.FILE_CHECKSUM,
                    ),
                )
            ),
            files=_Files(valid=False),  # type: ignore[arg-type]
        )
        gate = (await coordinator.reconcile(release))[0]
        blocked = replace(release, gates=(gate,))

        assert gate.status is GateStatus.FAILED
        assert publication_readiness(blocked)["publication_permitted"] is False
        with pytest.raises(ContractError):
            ApplicationDistributionService._require_publishable(blocked)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("outcome", "expected"),
    (
        (VerificationOutcome.FAIL, GateStatus.FAILED),
        (VerificationOutcome.NEEDS_CHANGES, GateStatus.FAILED),
        (VerificationOutcome.INCONCLUSIVE, GateStatus.INCONCLUSIVE),
    ),
)
def test_nonpassing_verification_outcomes_block_release(
    outcome: VerificationOutcome,
    expected: GateStatus,
) -> None:
    async def scenario() -> None:
        release = _release("verification")
        coordinator, verification = _verification_coordinator(release)
        pending = (await coordinator.reconcile(release))[0]
        verification_id = pending.details["verification_id"]
        assert isinstance(verification_id, str)
        _submit_verification(verification, verification_id, outcome)

        gate = (await coordinator.reconcile(replace(release, gates=(pending,))))[0]
        blocked = replace(release, gates=(gate,))
        assert gate.status is expected
        assert publication_readiness(blocked)["publication_permitted"] is False
        with pytest.raises(ContractError):
            ApplicationDistributionService._require_publishable(blocked)

    asyncio.run(scenario())


def test_verification_recovery_reconstructs_lost_projection_without_duplicate_request() -> None:
    async def scenario() -> None:
        release = _release("verification")
        coordinator, verification = _verification_coordinator(release)
        pending = (await coordinator.reconcile(release))[0]
        verification_id = pending.details["verification_id"]
        assert isinstance(verification_id, str)
        _submit_verification(verification, verification_id, VerificationOutcome.PASS)

        recovered = (await coordinator.reconcile(replace(release, gates=())))[0]
        again = (await coordinator.reconcile(replace(release, gates=(recovered,))))[0]
        history = verification.history(task_id=release.artifacts[0].build_task_id)

        assert recovered.status is GateStatus.PASSED
        assert recovered.details["verification_id"] == verification_id
        assert again == recovered
        assert len(history) == 1

    asyncio.run(scenario())


def test_source_and_build_spec_changes_require_fresh_verification() -> None:
    async def scenario() -> None:
        release = _release("verification")
        coordinator, verification = _verification_coordinator(release)
        pending = (await coordinator.reconcile(release))[0]
        verification_id = pending.details["verification_id"]
        assert isinstance(verification_id, str)
        _submit_verification(verification, verification_id, VerificationOutcome.PASS)
        passed = (await coordinator.reconcile(replace(release, gates=(pending,))))[0]

        changed_source = replace(
            release,
            source_revision="fedcba9876543210fedcba9876543210fedcba98",
            gates=(passed,),
        )
        source_gate = (await coordinator.reconcile(changed_source))[0]
        assert source_gate.status is GateStatus.PENDING
        assert source_gate.details["verification_id"] != verification_id

        changed_spec = replace(
            release,
            build_specification=replace(release.build_specification, revision=2),
            gates=(passed,),
        )
        spec_gate = (await coordinator.reconcile(changed_spec))[0]
        assert spec_gate.status is GateStatus.PENDING
        assert spec_gate.details["verification_id"] != verification_id

    asyncio.run(scenario())


def test_evaluation_failure_mismatch_unavailable_and_optional_absence_are_fail_closed() -> None:
    async def scenario() -> None:
        release = _release("evaluation")
        artifact = release.artifacts[0]
        reference = VersionReference(
            kind="application_release_artifact",
            ref_id=artifact.artifact_id,
            version=artifact.sha256,
            revision=artifact_subject_revision(release, artifact),
        )
        requirement = ReleaseGateRequirement(
            name="evaluation",
            kind=ReleaseGateKind.EVALUATION,
            target_id=artifact.target_id,
            evaluation_suite_id="release-suite",
            evaluation_suite_version="1",
        )

        missing_provider = ApplicationReleaseGateCoordinator(
            policy=StaticReleaseGatePolicy((requirement,)),
            files=_Files(),  # type: ignore[arg-type]
        )
        unavailable = (await missing_provider.reconcile(release))[0]
        assert unavailable.status is GateStatus.INCONCLUSIVE

        evaluations = InMemoryEvaluationRepository()
        mismatch = EvaluationRun(
            suite_id="release-suite",
            suite_version="1",
            snapshot=ConfigurationSnapshot(
                platform_version="test",
                references=(replace(reference, revision="different-subject"),),
            ),
            status=EvaluationRunStatus.COMPLETED,
            completed_at=datetime.now(UTC),
        )
        evaluations.save_run(mismatch)
        coordinator = ApplicationReleaseGateCoordinator(
            policy=StaticReleaseGatePolicy((requirement,)),
            files=_Files(),  # type: ignore[arg-type]
            evaluations=evaluations,
        )
        pending = (await coordinator.reconcile(release))[0]
        assert pending.status is GateStatus.PENDING

        failed = EvaluationRun(
            suite_id="release-suite",
            suite_version="1",
            snapshot=ConfigurationSnapshot(platform_version="test", references=(reference,)),
            status=EvaluationRunStatus.FAILED,
            completed_at=datetime.now(UTC),
        )
        evaluations.save_run(failed)
        evaluations.save_result(
            EvaluationResult(
                evaluation_run_id=failed.run_id,
                case_id="release-case",
                case_version="1",
                evaluator=EvaluatorDescriptor(
                    evaluator_id="deterministic-release-evaluator",
                    kind=EvaluatorKind.DETERMINISTIC,
                    version="1",
                    deterministic=True,
                ),
                outcome=EvaluationOutcome.FAILED,
                artifact_refs=(artifact.artifact_id,),
            )
        )
        failed_gate = (await coordinator.reconcile(release))[0]
        assert failed_gate.status is GateStatus.FAILED

        optional_release = _release()
        assert publication_readiness(optional_release)["publication_permitted"] is True
        ApplicationDistributionService._require_publishable(optional_release)

    asyncio.run(scenario())
