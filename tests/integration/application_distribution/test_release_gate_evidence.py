from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform import __version__
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
    GateEvidence,
    GateStatus,
    JsonApplicationReleaseRepository,
    PackageType,
    ReleaseChannel,
    ReleaseGateKind,
    ReleaseGateRequirement,
    ReleaseStatus,
    ReleaseVisibility,
    StaticReleaseGatePolicy,
    artifact_subject_revision,
    bind_gate_to_release,
    gate_is_current,
    publication_readiness,
    release_subject_digest,
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
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid

    async def verify_checksum(self, file_id: str, context: object) -> bool:
        del file_id, context
        return self.valid


def _release(*gate_names: str, sha256: str = "a" * 64) -> ApplicationRelease:
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
        external_metadata={
            "worker_id": "worker-7",
            "executor_version": "1.4.2",
            "host_path": "/must/not/leak",
            "token": "must-not-leak",
        },
    )
    specification = BuildSpecification(
        command=("python", "-m", "build"),
        targets=(target,),
        spec_id=new_id("build_spec"),
        test_gates=tuple(gate_names),
    )
    return ApplicationRelease(
        application_id="example-app",
        display_name="Example App",
        version="1.0.0",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PUBLIC,
        project_id=new_id("project"),
        workspace_id=new_id("workspace"),
        workspace_snapshot_id=new_id("workspace_snapshot"),
        workspace_content_checksum="c" * 64,
        source_revision="0123456789abcdef0123456789abcdef01234567",
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


def test_deterministic_gates_are_stable_and_runtime_provenance_is_allowlisted() -> None:
    async def scenario() -> None:
        release = _release("artifact", "manifest")
        coordinator = ApplicationReleaseGateCoordinator(
            policy=StaticReleaseGatePolicy(
                (
                    ReleaseGateRequirement(
                        name="artifact",
                        kind=ReleaseGateKind.DETERMINISTIC,
                        target_id="linux-x64",
                        deterministic_check=DeterministicGateCheck.ARTIFACT_EXISTS,
                    ),
                    ReleaseGateRequirement(
                        name="manifest",
                        kind=ReleaseGateKind.DETERMINISTIC,
                        deterministic_check=DeterministicGateCheck.MANIFEST_CHECKSUM,
                    ),
                )
            ),
            files=_Files(),  # type: ignore[arg-type]
        )

        first = await coordinator.reconcile(release)
        reconciled = replace(release, gates=first)
        second = await coordinator.reconcile(reconciled)

        assert first == second
        assert all(gate.status is GateStatus.PASSED for gate in first)
        artifact_gate = next(gate for gate in first if gate.name == "artifact")
        runtime = artifact_gate.details["runtime_provenance"]
        assert isinstance(runtime, dict)
        assert runtime == {"worker_id": "worker-7", "executor_version": "1.4.2"}
        manifest_gate = next(gate for gate in first if gate.name == "manifest")
        assert manifest_gate.details["manifest_subject_sha256"] == release_subject_digest(release)

    asyncio.run(scenario())


def test_verification_gate_reuses_exact_subject_and_rechecks_changed_artifact() -> None:
    async def scenario() -> None:
        release = _release("verification")
        verification = VerificationService()
        policy = verification.register_policy(
            VerificationPolicy(
                name="application release verification",
                stages=(
                    VerificationStage(
                        stage_id="release-review",
                        verifier_kind=VerifierKind.DETERMINISTIC,
                    ),
                ),
            )
        )
        access = CanonicalVerificationAccess(verification)
        coordinator = ApplicationReleaseGateCoordinator(
            policy=StaticReleaseGatePolicy(
                (
                    ReleaseGateRequirement(
                        name="verification",
                        kind=ReleaseGateKind.VERIFICATION,
                        target_id="linux-x64",
                        verification_policy_id=policy.policy_id,
                        verification_policy_version=policy.version,
                        verification_stage_id="release-review",
                    ),
                )
            ),
            files=_Files(),  # type: ignore[arg-type]
            verification_access=access,
        )

        pending = (await coordinator.reconcile(release))[0]
        assert pending.status is GateStatus.PENDING
        verification_id = pending.details["verification_id"]
        assert isinstance(verification_id, str)
        request = verification.get_request(verification_id)
        access.submit_result(
            VerificationResult(
                verification_id=verification_id,
                verifier=VerifierIdentity(
                    verifier_ref="deterministic:release-check",
                    kind=VerifierKind.DETERMINISTIC,
                    read_only=True,
                ),
                outcome=VerificationOutcome.PASS,
                subject=request.subject,
                checks_executed=("release_check",),
            )
        )

        passed = (await coordinator.reconcile(replace(release, gates=(pending,))))[0]
        assert passed.status is GateStatus.PASSED
        assert passed.details["verification_id"] == verification_id
        assert passed.details["artifact_sha256"] == "a" * 64

        changed_artifact = replace(release.artifacts[0], sha256="b" * 64)
        changed = replace(release, artifacts=(changed_artifact,), gates=(passed,))
        refreshed = (await coordinator.reconcile(changed))[0]
        assert refreshed.status is GateStatus.PENDING
        assert refreshed.details["verification_id"] != verification_id
        assert refreshed.details["artifact_sha256"] == "b" * 64

    asyncio.run(scenario())


def test_evaluation_gate_accepts_only_exact_subject_and_detects_conflicting_evidence() -> None:
    async def scenario() -> None:
        release = _release("evaluation")
        artifact = release.artifacts[0]
        evaluations = InMemoryEvaluationRepository()
        reference = VersionReference(
            kind="application_release_artifact",
            ref_id=artifact.artifact_id,
            version=artifact.sha256,
            revision=artifact_subject_revision(release, artifact),
        )
        snapshot = ConfigurationSnapshot(platform_version=__version__, references=(reference,))
        completed_at = datetime.now(UTC)
        passed_run = EvaluationRun(
            suite_id="release-suite",
            suite_version="1",
            snapshot=snapshot,
            status=EvaluationRunStatus.COMPLETED,
            completed_at=completed_at,
        )
        evaluations.save_run(passed_run)
        evaluations.save_result(
            EvaluationResult(
                evaluation_run_id=passed_run.run_id,
                case_id="release-case",
                case_version="1",
                evaluator=EvaluatorDescriptor(
                    evaluator_id="deterministic-release-evaluator",
                    kind=EvaluatorKind.DETERMINISTIC,
                    version="1",
                    deterministic=True,
                ),
                outcome=EvaluationOutcome.PASSED,
                artifact_refs=(artifact.artifact_id,),
            )
        )
        coordinator = ApplicationReleaseGateCoordinator(
            policy=StaticReleaseGatePolicy(
                (
                    ReleaseGateRequirement(
                        name="evaluation",
                        kind=ReleaseGateKind.EVALUATION,
                        target_id="linux-x64",
                        evaluation_suite_id="release-suite",
                        evaluation_suite_version="1",
                    ),
                )
            ),
            files=_Files(),  # type: ignore[arg-type]
            evaluations=evaluations,
        )

        passed = (await coordinator.reconcile(release))[0]
        assert passed.status is GateStatus.PASSED
        assert passed_run.run_id in passed.evidence_refs

        failed_run = EvaluationRun(
            suite_id="release-suite",
            suite_version="1",
            snapshot=snapshot,
            status=EvaluationRunStatus.FAILED,
            completed_at=datetime.now(UTC),
        )
        evaluations.save_run(failed_run)
        conflicting = (await coordinator.reconcile(release))[0]
        assert conflicting.status is GateStatus.INCONCLUSIVE
        assert "conflicting" in str(conflicting.details["blocking_reason"])

    asyncio.run(scenario())


def test_publication_readiness_and_publication_fail_closed_on_stale_subject() -> None:
    release = _release("verification")
    bound = bind_gate_to_release(
        GateEvidence(
            name="verification",
            status=GateStatus.PASSED,
            evidence_refs=("verification_ref",),
        ),
        release,
    )
    ready = replace(release, gates=(bound,))
    assert gate_is_current(bound, ready)
    assert publication_readiness(ready)["publication_permitted"] is True
    ApplicationDistributionService._require_publishable(ready)

    changed = replace(ready, artifacts=(replace(ready.artifacts[0], sha256="b" * 64),))
    state = publication_readiness(changed)
    assert state["publication_permitted"] is False
    assert state["blocking_reasons"]
    with pytest.raises(ContractError, match="stale"):
        ApplicationDistributionService._require_publishable(changed)


def test_gate_evidence_survives_release_repository_restart(tmp_path) -> None:
    async def scenario() -> None:
        path = tmp_path / "application-releases.json"
        release = _release("verification")
        gate = bind_gate_to_release(
            GateEvidence(
                name="verification",
                status=GateStatus.INCONCLUSIVE,
                evidence_refs=("verification_ref", "result_ref"),
                details={"blocking_reason": "conflicting evidence"},
            ),
            release,
        )
        stored = replace(release, gates=(gate,))
        first = JsonApplicationReleaseRepository(path)
        await first.save(stored, expected_revision=0)

        restored = await JsonApplicationReleaseRepository(path).get(release.release_id)
        assert restored.gates == stored.gates
        assert restored.gates[0].status is GateStatus.INCONCLUSIVE
        assert gate_is_current(restored.gates[0], restored)

    asyncio.run(scenario())
