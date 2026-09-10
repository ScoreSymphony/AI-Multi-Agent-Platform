from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from ai_multi_agent_platform.application_distribution import (
    APPLICATION_BUILD_ACTION,
    ApplicationCommandExecutor,
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
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.execution import ExecutionRequest
from ai_multi_agent_platform.security import SecretReference


class _RecordingGitHubTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, object] | None = None,
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
