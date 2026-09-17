from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from ai_multi_agent_platform.applications import (
    ApplicationHealthCheck,
    ApplicationHealthCheckKind,
    ApplicationInstallRequest,
    ApplicationManifest,
    ApplicationObservedState,
    ApplicationPreparationError,
    ApplicationRuntimeError,
    ApplicationRuntimeRegistry,
    ApplicationService,
    ApplicationServiceRuntime,
    ApplicationVolume,
    ApplicationVolumeBinding,
    ApplicationVolumeKind,
    ApplicationVolumeMount,
    InMemoryApplicationRepository,
    LocalApplicationWorkspaceBinder,
    LocalProcessApplicationRuntime,
)
from ai_multi_agent_platform.applications.service import ApplicationLifecycleService
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.workspaces import (
    LocalWorkspaceProvider,
    WorkspaceAccessMode,
    WorkspaceFile,
    WorkspaceType,
)

pytestmark = pytest.mark.asyncio


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="application-workspace-test",
            owner_type="user",
            owner_id="workspace-user",
            project_id=project_id,
        ),
        actor_ref="user:workspace-user",
    )


async def _workspace(
    tmp_path: Path,
    *,
    access_mode: WorkspaceAccessMode = WorkspaceAccessMode.READ_WRITE,
) -> tuple[LocalFileProvider, LocalWorkspaceProvider, DataAccessContext, str]:
    project_id = new_id("project")
    context = _context(project_id)
    files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
    source = await files.create_file(b"hello\n", context, content_type="text/plain")
    provider = LocalWorkspaceProvider(tmp_path / "workspaces", files)
    workspace = await provider.create_workspace(
        project_id=project_id,
        owner_ref=OwnerRef(type="user", id="workspace-user"),
        workspace_type=(
            WorkspaceType.READ_ONLY_SOURCE
            if access_mode is WorkspaceAccessMode.READ_ONLY
            else WorkspaceType.PERSISTENT_PROJECT
        ),
        access_mode=access_mode,
        context=context,
        files=(
            WorkspaceFile(
                relative_path="input.txt",
                file_id=source.file_id,
                sha256=source.sha256,
            ),
        ),
    )
    return files, provider, context, workspace.id


def _manifest(service: ApplicationService) -> ApplicationManifest:
    return ApplicationManifest(
        application_id=new_id("application"),
        name="Workspace process fixture",
        version="1.0.0",
        description="Application Workspace execution fixture",
        services=(service,),
        volumes=(ApplicationVolume(name="workspace", kind=ApplicationVolumeKind.WORKSPACE),),
        runtime_requirements=("local", "process", "workspace_cwd"),
    )


async def _lifecycle(
    provider: LocalWorkspaceProvider,
) -> tuple[LocalProcessApplicationRuntime, ApplicationLifecycleService]:
    runtime = LocalProcessApplicationRuntime(
        stop_timeout_seconds=1.0,
        workspace_binder=LocalApplicationWorkspaceBinder(provider, provider.local_path),
    )
    lifecycle = ApplicationLifecycleService(
        InMemoryApplicationRepository(),
        ApplicationRuntimeRegistry((runtime,)),
    )
    return runtime, lifecycle


async def _wait_for_log(
    lifecycle: ApplicationLifecycleService,
    instance_id: str,
    expected: str,
) -> None:
    for _ in range(100):
        entries = await lifecycle.logs(instance_id)
        if any(entry.message == expected for entry in entries):
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"log entry was not observed: {expected!r}")


async def test_local_process_runtime_uses_workspace_as_cwd_and_commits_changes(
    tmp_path: Path,
) -> None:
    files, workspaces, context, workspace_id = await _workspace(tmp_path)
    _, lifecycle = await _lifecycle(workspaces)
    script = (
        "from pathlib import Path; import time; "
        "assert Path('input.txt').read_text() == 'hello\\n'; "
        "Path('result.txt').write_text('written'); "
        "print('workspace-written', flush=True); time.sleep(30)"
    )
    service = ApplicationService(
        service_id="app",
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-u"),
        command=("-c", script),
        mounts=(ApplicationVolumeMount(volume_name="workspace", target="."),),
    )
    manifest = _manifest(service)
    instance = await lifecycle.install(
        ApplicationInstallRequest(
            manifest=manifest,
            volume_bindings=(
                ApplicationVolumeBinding(
                    volume_name="workspace",
                    kind=ApplicationVolumeKind.WORKSPACE,
                    source_ref=workspace_id,
                ),
            ),
        ),
        runtime_id="local.process",
    )

    running = await lifecycle.start(instance.instance_id)
    assert running.observed_state is ApplicationObservedState.RUNNING
    await _wait_for_log(lifecycle, instance.instance_id, "workspace-written")
    stopped = await lifecycle.stop(instance.instance_id)
    assert stopped.observed_state is ApplicationObservedState.STOPPED

    workspace = await workspaces.get_workspace(workspace_id)
    assert workspace.revision == 1
    assert workspace.base_snapshot_id is not None
    snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id)
    result = next(item for item in snapshot.files if item.relative_path == "result.txt")
    assert await files.read(result.file_id, context.operation) == b"written"
    assert tuple(workspaces.materialization_root.iterdir()) == ()
    assert stopped.volume_bindings[0].source_ref == workspace_id
    assert str(workspaces.materialization_root) not in repr(stopped)


async def test_local_process_runtime_reads_canonical_read_only_workspace(
    tmp_path: Path,
) -> None:
    _, workspaces, _, workspace_id = await _workspace(
        tmp_path,
        access_mode=WorkspaceAccessMode.READ_ONLY,
    )
    _, lifecycle = await _lifecycle(workspaces)
    service = ApplicationService(
        service_id="app",
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-u"),
        command=(
            "-c",
            "from pathlib import Path; import time; "
            "print(Path('input.txt').read_text().strip(), flush=True); time.sleep(30)",
        ),
        mounts=(
            ApplicationVolumeMount(
                volume_name="workspace",
                target=".",
                read_only=True,
            ),
        ),
    )
    manifest = _manifest(service)
    instance = await lifecycle.install(
        ApplicationInstallRequest(
            manifest=manifest,
            volume_bindings=(
                ApplicationVolumeBinding(
                    volume_name="workspace",
                    kind=ApplicationVolumeKind.WORKSPACE,
                    source_ref=workspace_id,
                    read_only=True,
                ),
            ),
        ),
        runtime_id="local.process",
    )

    await lifecycle.start(instance.instance_id)
    await _wait_for_log(lifecycle, instance.instance_id, "hello")
    await lifecycle.stop(instance.instance_id)

    workspace = await workspaces.get_workspace(workspace_id)
    assert workspace.revision == 0
    assert tuple(workspaces.materialization_root.iterdir()) == ()


async def test_local_process_runtime_rejects_arbitrary_workspace_mount_target(
    tmp_path: Path,
) -> None:
    _, workspaces, _, workspace_id = await _workspace(tmp_path)
    runtime, _ = await _lifecycle(workspaces)
    service = ApplicationService(
        service_id="app",
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-c", "pass"),
        mounts=(ApplicationVolumeMount(volume_name="workspace", target="/workspace"),),
    )

    with pytest.raises(ApplicationPreparationError, match="mount target '.'"):
        await runtime.prepare(
            ApplicationInstallRequest(
                manifest=_manifest(service),
                volume_bindings=(
                    ApplicationVolumeBinding(
                        volume_name="workspace",
                        kind=ApplicationVolumeKind.WORKSPACE,
                        source_ref=workspace_id,
                    ),
                ),
            )
        )


async def test_local_process_runtime_rejects_read_only_projection_of_writable_workspace(
    tmp_path: Path,
) -> None:
    _, workspaces, _, workspace_id = await _workspace(tmp_path)
    _, lifecycle = await _lifecycle(workspaces)
    service = ApplicationService(
        service_id="app",
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-c", "import time; time.sleep(30)"),
        mounts=(
            ApplicationVolumeMount(
                volume_name="workspace",
                target=".",
                read_only=True,
            ),
        ),
    )
    manifest = _manifest(service)
    instance = await lifecycle.install(
        ApplicationInstallRequest(
            manifest=manifest,
            volume_bindings=(
                ApplicationVolumeBinding(
                    volume_name="workspace",
                    kind=ApplicationVolumeKind.WORKSPACE,
                    source_ref=workspace_id,
                    read_only=True,
                ),
            ),
        ),
        runtime_id="local.process",
    )

    with pytest.raises(ApplicationRuntimeError, match="cannot enforce a read-only projection"):
        await lifecycle.start(instance.instance_id)
    failed = await lifecycle.status(instance.instance_id)
    assert failed.observed_state is ApplicationObservedState.FAILED
    assert (await workspaces.get_workspace(workspace_id)).revision == 0
    assert tuple(workspaces.materialization_root.iterdir()) == ()


async def test_startup_failure_releases_workspace_without_committing_partial_changes(
    tmp_path: Path,
) -> None:
    _, workspaces, _, workspace_id = await _workspace(tmp_path)
    _, lifecycle = await _lifecycle(workspaces)
    service = ApplicationService(
        service_id="app",
        runtime=ApplicationServiceRuntime.PROCESS,
        process=(sys.executable, "-u"),
        command=(
            "-c",
            "from pathlib import Path; import time; "
            "Path('partial.txt').write_text('discard-me'); time.sleep(30)",
        ),
        mounts=(ApplicationVolumeMount(volume_name="workspace", target="."),),
        health_check=ApplicationHealthCheck(
            kind=ApplicationHealthCheckKind.COMMAND,
            command=(sys.executable, "-c", "import sys; sys.exit(1)"),
            interval_seconds=0.02,
            timeout_seconds=1.0,
            retries=2,
        ),
    )
    manifest = _manifest(service)
    instance = await lifecycle.install(
        ApplicationInstallRequest(
            manifest=manifest,
            volume_bindings=(
                ApplicationVolumeBinding(
                    volume_name="workspace",
                    kind=ApplicationVolumeKind.WORKSPACE,
                    source_ref=workspace_id,
                ),
            ),
        ),
        runtime_id="local.process",
    )

    with pytest.raises(ApplicationRuntimeError, match="failed its startup health check"):
        await lifecycle.start(instance.instance_id)
    workspace = await workspaces.get_workspace(workspace_id)
    assert workspace.revision == 0
    assert workspace.base_snapshot_id is not None
    snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id)
    assert all(item.relative_path != "partial.txt" for item in snapshot.files)
    assert tuple(workspaces.materialization_root.iterdir()) == ()
