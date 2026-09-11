from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

from ai_multi_agent_platform import __version__
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
    artifact_subject_revision,
    publication_readiness,
)
from ai_multi_agent_platform.application_distribution.evaluation_gate_orchestration import (
    evaluation_snapshot_fingerprint,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.evaluation import (
    ComparisonOperator,
    ConfigurationSnapshot,
    DeterministicAssertion,
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationObservation,
    EvaluationOutcome,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunner,
    EvaluationRunStatus,
    EvaluationService,
    EvaluationSuite,
    EvaluatorDescriptor,
    EvaluatorKind,
    InMemoryEvaluationRepository,
    SnapshotValue,
    VersionReference,
)
from ai_multi_agent_platform.evaluation.context import EvaluationExecutionContext


class _Files:
    async def verify_checksum(self, file_id: str, context: object) -> bool:
        del file_id, context
        return True


class _PassingExecutor:
    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case, attempt, execution_context
        return EvaluationObservation(data={"release_ok": True})


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
        application_id="evaluation-fingerprint-app",
        display_name="Evaluation Fingerprint App",
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
            spec_id=new_id("build_spec"),
            test_gates=("evaluation",),
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


def _requirement() -> ReleaseGateRequirement:
    return ReleaseGateRequirement(
        name="evaluation",
        kind=ReleaseGateKind.EVALUATION,
        target_id="linux-x64",
        evaluation_suite_id="release-suite",
        evaluation_suite_version="1",
    )


def _mismatched_passing_run(
    repository: InMemoryEvaluationRepository,
    release: ApplicationRelease,
) -> EvaluationRun:
    artifact = release.artifacts[0]
    run = EvaluationRun(
        suite_id="release-suite",
        suite_version="1",
        snapshot=ConfigurationSnapshot(
            platform_version=__version__,
            platform_commit="different-platform-commit",
            references=(
                VersionReference(
                    kind="application_release_artifact",
                    ref_id=artifact.artifact_id,
                    version=artifact.sha256,
                    revision=artifact_subject_revision(release, artifact),
                ),
            ),
            environment=(SnapshotValue(key="release-profile", value="different"),),
        ),
        status=EvaluationRunStatus.COMPLETED,
        completed_at=datetime.now(UTC),
    )
    repository.save_run(run)
    repository.save_result(
        EvaluationResult(
            evaluation_run_id=run.run_id,
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
    return run


def _evaluation_service(repository: InMemoryEvaluationRepository) -> EvaluationService:
    suite = EvaluationSuite(
        suite_id="release-suite",
        name="Release suite",
        version="1",
        cases=(
            EvaluationCase(
                case_id="release-case",
                name="Release case",
                version="1",
                assertions=(
                    DeterministicAssertion(
                        assertion_id="release-ok",
                        path="release_ok",
                        operator=ComparisonOperator.EQ,
                        expected=True,
                    ),
                ),
            ),
        ),
    )
    return EvaluationService(
        repository=repository,
        runner=EvaluationRunner(
            repository=repository,
            executor=_PassingExecutor(),
            evaluators=(DeterministicAssertionEvaluator(),),
        ),
        suites=(suite,),
    )


def test_mismatched_evaluation_configuration_cannot_satisfy_release_gate() -> None:
    async def scenario() -> None:
        release = _release()
        repository = InMemoryEvaluationRepository()
        mismatched = _mismatched_passing_run(repository, release)
        coordinator = ApplicationReleaseGateCoordinator(
            policy=StaticReleaseGatePolicy((_requirement(),)),
            files=_Files(),  # type: ignore[arg-type]
            evaluations=repository,
        )

        gate = (await coordinator.reconcile(release))[0]
        blocked = replace(release, gates=(gate,))

        assert gate.status is GateStatus.PENDING
        assert mismatched.run_id not in gate.evidence_refs
        assert gate.details["evaluation_snapshot_fingerprint"] != (
            evaluation_snapshot_fingerprint(mismatched.snapshot)
        )
        assert publication_readiness(blocked)["publication_permitted"] is False

    asyncio.run(scenario())


def test_mismatched_configuration_drives_fresh_exact_snapshot_run() -> None:
    async def scenario() -> None:
        release = _release()
        repository = InMemoryEvaluationRepository()
        mismatched = _mismatched_passing_run(repository, release)
        service = _evaluation_service(repository)
        coordinator = ApplicationReleaseGateCoordinator(
            policy=StaticReleaseGatePolicy((_requirement(),)),
            files=_Files(),  # type: ignore[arg-type]
            evaluations=repository,
            evaluation_service=service,
        )

        gate = (await coordinator.reconcile(release))[0]
        runs = repository.list_runs(suite_id="release-suite", suite_version="1")

        assert gate.status is GateStatus.PASSED
        assert len(runs) == 2
        assert gate.details["evaluation_run_id"] != mismatched.run_id
        exact_run = next(run for run in runs if run.run_id == gate.details["evaluation_run_id"])
        assert exact_run.snapshot.platform_commit is None
        assert exact_run.snapshot.environment == ()
        assert gate.details["evaluation_snapshot_fingerprint"] == (
            evaluation_snapshot_fingerprint(exact_run.snapshot)
        )

    asyncio.run(scenario())
