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
    artifact_subject_revision,
    publication_readiness,
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
    EvaluationRunner,
    EvaluationService,
    EvaluationSuite,
    InMemoryEvaluationRepository,
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


def _release(sha256: str = "a" * 64) -> ApplicationRelease:
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
    return ApplicationRelease(
        application_id="evaluation-gate-app",
        display_name="Evaluation Gate App",
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


def _evaluation_service(
    repository: InMemoryEvaluationRepository,
) -> EvaluationService:
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
    runner = EvaluationRunner(
        repository=repository,
        executor=_PassingExecutor(),
        evaluators=(DeterministicAssertionEvaluator(),),
    )
    return EvaluationService(
        repository=repository,
        runner=runner,
        suites=(suite,),
    )


def _coordinator(
    repository: InMemoryEvaluationRepository,
    service: EvaluationService,
) -> ApplicationReleaseGateCoordinator:
    return ApplicationReleaseGateCoordinator(
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
        evaluations=repository,
        evaluation_service=service,
    )


def test_missing_evaluation_gate_is_run_automatically_and_reused() -> None:
    async def scenario() -> None:
        release = _release()
        repository = InMemoryEvaluationRepository()
        coordinator = _coordinator(repository, _evaluation_service(repository))

        first, second = await asyncio.gather(
            coordinator.reconcile(release),
            coordinator.reconcile(release),
        )

        runs = repository.list_runs(suite_id="release-suite", suite_version="1")
        assert len(runs) == 1
        assert first[0].status is GateStatus.PASSED
        assert second[0].status is GateStatus.PASSED
        reference = next(
            item
            for item in runs[0].snapshot.references
            if item.kind == "application_release_artifact"
        )
        artifact = release.artifacts[0]
        assert reference.ref_id == artifact.artifact_id
        assert reference.version == artifact.sha256
        assert reference.revision == artifact_subject_revision(release, artifact)

        again = await coordinator.reconcile(replace(release, gates=first))
        assert again[0].status is GateStatus.PASSED
        assert len(repository.list_runs(suite_id="release-suite", suite_version="1")) == 1

    asyncio.run(scenario())


def test_changed_artifact_drives_fresh_exact_subject_evaluation() -> None:
    async def scenario() -> None:
        release = _release()
        repository = InMemoryEvaluationRepository()
        coordinator = _coordinator(repository, _evaluation_service(repository))
        first = await coordinator.reconcile(release)

        changed = replace(
            release,
            artifacts=(replace(release.artifacts[0], sha256="b" * 64),),
            gates=first,
        )
        refreshed = await coordinator.reconcile(changed)

        assert refreshed[0].status is GateStatus.PASSED
        runs = repository.list_runs(suite_id="release-suite", suite_version="1")
        assert len(runs) == 2
        artifact = changed.artifacts[0]
        assert any(
            reference.kind == "application_release_artifact"
            and reference.ref_id == artifact.artifact_id
            and reference.version == artifact.sha256
            and reference.revision == artifact_subject_revision(changed, artifact)
            for run in runs
            for reference in run.snapshot.references
        )

    asyncio.run(scenario())


def test_missing_configured_evaluation_suite_stays_fail_closed() -> None:
    async def scenario() -> None:
        release = _release()
        repository = InMemoryEvaluationRepository()
        empty_service = EvaluationService(
            repository=repository,
            runner=EvaluationRunner(
                repository=repository,
                executor=_PassingExecutor(),
                evaluators=(DeterministicAssertionEvaluator(),),
            ),
            suites=(),
        )
        coordinator = _coordinator(repository, empty_service)

        gates = await coordinator.reconcile(release)
        blocked = replace(release, gates=gates)

        assert gates[0].status is GateStatus.PENDING
        assert repository.list_runs(suite_id="release-suite", suite_version="1") == ()
        assert publication_readiness(blocked)["publication_permitted"] is False

    asyncio.run(scenario())
