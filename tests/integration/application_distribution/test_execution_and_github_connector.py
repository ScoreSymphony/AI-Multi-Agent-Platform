from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from ai_multi_agent_platform.application_distribution import (
    APPLICATION_BUILD_ACTION,
    ApplicationBuildLifecycleBackend,
    ApplicationCommandExecutor,
    ApplicationDistributionService,
    BuildSpecification,
    BuildTarget,
    BuildTargetStatus,
    InMemoryApplicationReleaseRepository,
    PackageType,
    ReleaseChannel,
    ReleaseStatus,
    ReleaseVisibility,
)
from ai_multi_agent_platform.configuration import LocalSecretProvider
from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectorActionInvocation,
    DurableGitHubReleaseConnectorProvider,
    GITHUB_RELEASE_CONNECTOR_TYPE,
    GITHUB_RELEASE_CONNECTOR_VERSION,
    GitHubRestResponse,
    InMemoryConnectorRepository,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, new_id
from ai_multi_agent_platform.execution import ExecutionRequest
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.security import SecretReference
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    LocalWorkspaceProvider,
    WorkspaceType,
)


class _RecordingGitHubTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, JsonValue] | None = None,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> GitHubRestResponse:
        del headers, json_body, data, content_type
        self.requests.append((method, url))
        if method == "GET" and url == "https://api.github.com/user":
            return GitHubRestResponse(status=200, body={"login": "tester", "id": 42})
        raise AssertionError(f"unexpected GitHub request: {method} {url}")


def test_application_command_executor_runs_explicit_argv_without_shell(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace_token = "materialized-build"
        workspace = tmp_path / workspace_token
        workspace.mkdir()
        executor = ApplicationCommandExecutor(tmp_path)
        request = ExecutionRequest(
            task_id=new_id("task"),
            run_id=new_id("run"),
            correlation_id="application-build-test",
            action=APPLICATION_BUILD_ACTION,
            workspace=workspace_token,
            arguments={
                "command": [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; "
                        "Path('dist').mkdir(); "
                        "Path('dist/app.bin').write_bytes(b'package')"
                    ),
                ],
                "output_path": "dist/app.bin",
            },
        )

        result = await executor.execute(request)

        assert result.status is RunStatus.SUCCEEDED
        assert result.result_code == 0
        assert result.artifacts[0].relative_path == "dist/app.bin"
        assert (workspace / "dist" / "app.bin").read_bytes() == b"package"
        assert executor.descriptor.metadata["shell"] is False

    asyncio.run(scenario())


def test_application_distribution_executes_build_and_admits_canonical_artifact(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        operation = OperationContext(
            correlation_id="application-build-vertical",
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
        specification = BuildSpecification(
            command=(
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('dist').mkdir(); "
                    "Path('dist/app.bin').write_bytes(b'vertical-package')"
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
        )
        release = await service.create_release(
            application_id="vertical-app",
            display_name="Vertical App",
            version="1.0.0",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PRIVATE,
            project_id=project_id,
            workspace_id=workspace.id,
            workspace_snapshot_id=snapshot.id,
            source_revision="source-revision-1",
            build_specification=specification,
            creator_ref="user:tester",
        )

        built = await service.request_build(
            release.release_id,
            target_id="local-test",
            idempotency_key="vertical-build-1",
            actor_ref="user:tester",
        )

        assert built.status is ReleaseStatus.READY
        assert built.targets[0].status is BuildTargetStatus.SUCCEEDED
        assert len(built.artifacts) == 1
        artifact = built.artifacts[0]
        assert artifact.filename == "app.bin"
        assert artifact.sha256
        assert artifact.build_run_id == built.targets[0].run_id
        assert artifact.build_task_id == built.targets[0].task_id
        assert built.targets[0].task_id is not None
        assert built.targets[0].run_id is not None
        run = await kernel.get_run(built.targets[0].task_id, built.targets[0].run_id)
        assert run.status is RunStatus.SUCCEEDED
        assert artifact.artifact_id in run.artifact_ids
        file_context = DataAccessContext(
            operation=operation,
            actor_ref="user:tester",
            task_id=built.targets[0].task_id,
            run_id=built.targets[0].run_id,
        )
        record = await files.get_file(artifact.file_id, file_context)
        assert record.sha256 == artifact.sha256
        assert artifact.artifact_id in record.artifact_ids
        assert await files.verify_checksum(artifact.file_id, file_context)

    asyncio.run(scenario())


def test_durable_github_connector_rehydrates_persisted_connection_before_action() -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        connection_id = new_id("connection")
        reference = SecretReference(
            provider="local-secrets",
            secret_id="github-release-token",
            scope=project_id,
        )
        secrets = LocalSecretProvider()
        await secrets.create(
            reference,
            "test-token",
            purpose="github-api-token",
            allowed_consumers=("connector.github-releases",),
            allowed_purposes=("github-api-token",),
        )
        repository = InMemoryConnectorRepository()
        await repository.save_connection(
            Connection(
                id=connection_id,
                connector_type_id=GITHUB_RELEASE_CONNECTOR_TYPE,
                connector_version=GITHUB_RELEASE_CONNECTOR_VERSION,
                owner_type="user",
                owner_id="tester",
                display_name="GitHub Releases",
                project_id=project_id,
                secret_references=(reference,),
            )
        )
        transport = _RecordingGitHubTransport()
        provider = DurableGitHubReleaseConnectorProvider(
            secrets,
            object(),  # type: ignore[arg-type]
            repository,
            transport=transport,
        )
        context = OperationContext(
            correlation_id="github-release-restart",
            owner_type="user",
            owner_id="tester",
            project_id=project_id,
        )

        with pytest.raises(ContractError) as raised:
            await provider.invoke_action(
                ConnectorActionInvocation(
                    invocation_id="github-restart-action",
                    connection_id=connection_id,
                    action="github.release.unsupported-test-action",
                    arguments={},
                    context=context,
                )
            )

        assert raised.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
        assert transport.requests == [("GET", "https://api.github.com/user")]

    asyncio.run(scenario())
