from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectorActionInvocation,
    ConnectorActionResult,
    ConnectorDefinition,
    ConnectorProvider,
    ConnectorResourceQuery,
    ConnectorSyncRequest,
    ConnectorSyncResult,
    ExternalNativeReference,
    ExternalResourceReference,
    connector_definition_id,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    HealthStatus,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories import ConnectorRepositoryProvider, RepositoryConnection

_SHA = "a" * 40


class _FakeForgeConnector(ConnectorProvider):
    def __init__(self, connection_id: str) -> None:
        self.connection_id = connection_id
        self.actions_seen: list[str] = []
        self.repository_id = new_id("external_resource")
        self.file_id = new_id("external_resource")

    @property
    def definition(self) -> ConnectorDefinition:
        return ConnectorDefinition(
            id=connector_definition_id("fake.forge", "1.0"),
            connector_type_id="fake.forge",
            name="Fake Forge",
            version="1.0",
            resource_types=("repository", "file"),
            actions=(
                "repository.resolve_revision",
                "repository.read_tree",
                "repository.branches",
                "repository.tags",
                "repository.commits",
                "repository.status",
            ),
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(provider_id="connector.fake-forge", provider_type="connector")

    async def validate_connection(
        self,
        connection: Connection,
        context: OperationContext,
    ) -> Connection:
        del context
        return connection

    async def connection_health(
        self,
        connection: Connection,
        context: OperationContext,
    ) -> HealthStatus:
        del connection, context
        return HealthStatus.HEALTHY

    async def list_resources(
        self,
        query: ConnectorResourceQuery,
    ) -> tuple[ExternalResourceReference, ...]:
        assert query.connection_id == self.connection_id
        assert query.resource_type == "repository"
        return (self._repository(),)

    async def read_resource(
        self,
        connection: Connection,
        resource: ExternalResourceReference,
        context: OperationContext,
    ) -> ExternalResourceReference:
        del context
        assert connection.id == self.connection_id
        assert resource.id == self.repository_id
        return self._repository()

    async def invoke_action(self, invocation: ConnectorActionInvocation) -> ConnectorActionResult:
        self.actions_seen.append(invocation.action)
        if invocation.action == "repository.resolve_revision":
            output = {"commit_sha": _SHA}
            resources: tuple[ExternalResourceReference, ...] = ()
        elif invocation.action == "repository.read_tree":
            output = {
                "resolved_revision": _SHA,
                "entries": [
                    {
                        "relative_path": "README.md",
                        "resource_id": self.file_id,
                    }
                ],
            }
            resources = (self._file(),)
        elif invocation.action == "repository.branches":
            output = {"branches": ["main", "feature/test"]}
            resources = ()
        elif invocation.action == "repository.tags":
            output = {"tags": ["v1.0.0"]}
            resources = ()
        elif invocation.action == "repository.commits":
            assert invocation.arguments["revision"] == "main"
            assert invocation.arguments["limit"] == 5
            output = {
                "commits": [
                    {
                        "revision": _SHA,
                        "message": "initial",
                        "parent_revisions": [],
                    }
                ]
            }
            resources = ()
        elif invocation.action == "repository.status":
            output = {
                "head_revision": _SHA,
                "branch": "main",
                "staged_paths": [],
                "modified_paths": [],
                "deleted_paths": [],
                "untracked_paths": [],
            }
            resources = ()
        else:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"unsupported fake forge action: {invocation.action}",
            )
        return ConnectorActionResult(
            invocation_id=invocation.invocation_id,
            output=output,
            resource_refs=resources,
        )

    async def synchronize(self, request: ConnectorSyncRequest) -> ConnectorSyncResult:
        del request
        raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY, "sync not supported")

    async def import_file_content(
        self,
        connection: Connection,
        resource: ExternalResourceReference,
        context: OperationContext,
    ) -> bytes:
        del context
        assert connection.id == self.connection_id
        assert resource.id == self.file_id
        return b"# repository\n"

    def _repository(self) -> ExternalResourceReference:
        return ExternalResourceReference(
            id=self.repository_id,
            connection_id=self.connection_id,
            resource_type="repository",
            native_reference=ExternalNativeReference(
                namespace="fake.forge",
                native_id="owner/repository",
            ),
            canonical_url="https://forge.invalid/owner/repository",
            revision=_SHA,
            metadata={"default_branch": "main", "visibility": "private"},
        )

    def _file(self) -> ExternalResourceReference:
        return ExternalResourceReference(
            id=self.file_id,
            connection_id=self.connection_id,
            resource_type="file",
            native_reference=ExternalNativeReference(
                namespace="fake.forge",
                native_id="owner/repository:README.md",
            ),
            revision=_SHA,
        )


def test_connector_repository_provider_is_provider_neutral_and_fail_closed() -> None:
    async def scenario() -> None:
        connection = Connection(
            id=new_id("connection"),
            connector_type_id="fake.forge",
            connector_version="1.0",
            owner_type="user",
            owner_id="repository-user",
            display_name="Fake Forge",
            project_id=new_id("project"),
        )
        repository_connection = RepositoryConnection(
            connection=connection,
            provider_id="repository-connector.fake-forge",
        )
        connector = _FakeForgeConnector(connection.id)
        provider = ConnectorRepositoryProvider(connector, repository_connection)
        context = OperationContext(
            correlation_id="issue-82-connector-repository",
            owner_type="user",
            owner_id="repository-user",
            project_id=connection.project_id,
        )

        repositories = await provider.discover(repository_connection, context)
        assert len(repositories) == 1
        repository = repositories[0]
        assert repository.id == connector.repository_id
        assert repository.default_branch == "main"
        assert repository.visibility.value == "private"
        assert repository.resolved_revision == _SHA
        assert provider.descriptor.provider_type == "repository"
        assert any(capability.operation.value == "repository.inspect_refs" for capability in repository.capabilities)

        resolved = await provider.resolve_revision(repository, "main", context)
        assert resolved.requested_ref == "main"
        assert resolved.commit_sha == _SHA

        tree = await provider.read_tree(repository, "main", context)
        assert tree.resolved_revision == _SHA
        assert len(tree.entries) == 1
        assert tree.entries[0].relative_path == "README.md"
        assert tree.entries[0].data == b"# repository\n"
        assert await provider.branches(repository, context) == ("main", "feature/test")
        assert await provider.tags(repository, context) == ("v1.0.0",)
        commits = await provider.commits(repository, context, revision="main", limit=5)
        assert len(commits) == 1
        assert commits[0].revision == _SHA
        assert commits[0].message == "initial"
        assert commits[0].parent_revisions == ()
        assert (await provider.status(repository, context)).clean

        with pytest.raises(ContractError) as exc_info:
            await provider.push(repository, context)
        assert exc_info.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
        assert "repository.push" not in connector.actions_seen

    asyncio.run(scenario())
