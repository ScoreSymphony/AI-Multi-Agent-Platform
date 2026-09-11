from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.application_distribution import (
    ApplicationBuildLifecycleBackend,
    ApplicationCommandExecutor,
    ApplicationDistributionService,
    ApplicationReleaseGateCoordinator,
    BuildSpecification,
    BuildTarget,
    DeterministicGateCheck,
    GateStatus,
    InMemoryApplicationReleaseRepository,
    PackageType,
    ReleaseChannel,
    ReleaseGateKind,
    ReleaseGateRequirement,
    ReleaseVisibility,
    StaticReleaseGatePolicy,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    LocalWorkspaceProvider,
    WorkspaceType,
)


async def _build_release(tmp_path: Path):  # type: ignore[no-untyped-def]
    project_id = new_id("project")
    operation = OperationContext(
        correlation_id="application-build-runtime-provenance",
        owner_type="user",
        owner_id="tester",
        project_id=project_id,
    )
    data_context = DataAccessContext(operation=operation, actor_ref="user:tester")
    files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
    workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
    workspace = await workspaces.create_workspace(
        project_id=project_id,
        owner_ref=OwnerRef(type="user", id="tester"),
        workspace_type=WorkspaceType.PERSISTENT_PROJECT,
        context=data_context,
    )
    snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id or "")
    releases = InMemoryApplicationReleaseRepository()
    bindings = InMemoryRunWorkspaceBindingRepository()
    lifecycle = ApplicationBuildLifecycleBackend(
        releases,
        workspaces,
        files,
        bindings,
        ApplicationCommandExecutor(workspaces.materialization_root),
    )
    kernel = PlatformKernel(
        orchestrator=ReferenceOrchestrator(),
        lifecycle=lifecycle,
        repository=InMemoryKernelRepository(),
    )
    service = ApplicationDistributionService(
        releases,
        kernel=kernel,
        files=files,
        workspaces=workspaces,
        run_workspace_bindings=bindings,
    )
    release = await service.create_release(
        application_id="runtime-provenance-app",
        display_name="Runtime Provenance App",
        version="1.0.0",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PRIVATE,
        project_id=project_id,
        workspace_id=workspace.id,
        workspace_snapshot_id=snapshot.id,
        source_revision="runtime-provenance-source",
        build_specification=BuildSpecification(
            command=(
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('dist').mkdir(); "
                    "Path('dist/app.bin').write_bytes(b'runtime-provenance')"
                ),
            ),
            targets=(
                BuildTarget(
                    target_id="local-test",
                    os_name="test",
                    architecture="test",
                    package_type=PackageType.ARCHIVE,
                    output_path="dist/app.bin",
                ),
            ),
            test_gates=("artifact-exists",),
        ),
        creator_ref="user:tester",
    )
    built = await service.request_build(
        release.release_id,
        target_id="local-test",
        idempotency_key="runtime-provenance-build",
        actor_ref="user:tester",
    )
    return service, releases, files, built


def test_real_build_captures_safe_runtime_provenance_and_projects_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        _service, _releases, files, built = await _build_release(tmp_path)
        artifact = built.artifacts[0]
        metadata = dict(artifact.external_metadata)

        assert metadata["executor_id"] == "application-command"
        assert metadata["build_provider_version"] == __version__
        assert isinstance(metadata.get("os"), str) and metadata["os"]
        assert isinstance(metadata.get("architecture"), str) and metadata["architecture"]
        assert "workspace_root" not in metadata
        assert "environment_allowlist" not in metadata

        coordinator = ApplicationReleaseGateCoordinator(
            policy=StaticReleaseGatePolicy(
                (
                    ReleaseGateRequirement(
                        name="artifact-exists",
                        kind=ReleaseGateKind.DETERMINISTIC,
                        target_id="local-test",
                        deterministic_check=DeterministicGateCheck.ARTIFACT_EXISTS,
                    ),
                )
            ),
            files=files,
        )
        gate = (await coordinator.reconcile(built))[0]

        assert gate.status is GateStatus.PASSED
        runtime = gate.details["runtime_provenance"]
        assert isinstance(runtime, dict)
        assert runtime["executor_id"] == "application-command"
        assert runtime["build_provider_version"] == __version__
        serialized = json.dumps(runtime, sort_keys=True)
        assert str(tmp_path) not in serialized
        assert "workspace_root" not in serialized

    asyncio.run(scenario())


def test_build_retry_recovers_runtime_provenance_from_canonical_run_output(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, releases, _files, built = await _build_release(tmp_path)
        artifact = built.artifacts[0]
        run_id = artifact.build_run_id
        stripped = replace(
            built,
            artifacts=(replace(artifact, external_metadata={}),),
            revision=built.revision + 1,
        )
        await releases.save(stripped, expected_revision=built.revision)

        recovered = await service.request_build(
            built.release_id,
            target_id="local-test",
            idempotency_key="runtime-provenance-recovery",
            actor_ref="user:tester",
        )

        recovered_artifact = recovered.artifacts[0]
        assert recovered_artifact.build_run_id == run_id
        assert recovered_artifact.external_metadata["executor_id"] == "application-command"
        assert recovered_artifact.external_metadata["build_provider_version"] == __version__

    asyncio.run(scenario())
