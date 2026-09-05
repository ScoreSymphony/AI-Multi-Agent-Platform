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
from ai_multi_agent_platform.repositories import (
    ConnectorRepositoryProvider,
    RepositoryBinding,
    RepositoryCallContext,
    RepositoryChangeRequestState,
    RepositoryConnection,
    RepositoryIssueState,
    RepositoryRegistry,
    RepositoryService,
)
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)

_SHA = "b" * 40


class _CollaborationConnector(ConnectorProvider):
    def __init__(self, connection_id: str) -> None:
        self.connection_id = connection_id
        self.repository_id = new_id("external_resource")
        self.issue_id = new_id("external_resource")
        self.change_request_id = new_id("external_resource")
        self.actions_seen: list[str] = []

    @property
    def definition(self) -> ConnectorDefinition:
        return ConnectorDefinition(
            id=connector_definition_id("fake.collaboration", "1.0"),
            connector_type_id="fake.collaboration",
            name="Fake Collaboration Forge",
            version="1.0",
            resource_types=("repository", "issue", "merge_request"),
            actions=(
                "repository.issue.read",
                "repository.issue.open",
                "repository.issue.update",
                "repository.change_request.read",
                "repository.change_request.open",
                "repository.change_request.update",
            ),
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="connector.fake-collaboration",
            provider_type="connector",
        )

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
        resources: tuple[ExternalResourceReference, ...]
        if invocation.action == "repository.issue.open":
            output = {"title": "Bug", "state": "open", "body": "details"}
            resources = (self._issue(),)
        elif invocation.action == "repository.issue.read":
            issue = invocation.arguments["issue"]
            assert isinstance(issue, dict)
            assert issue["resource_type"] == "repository_issue"
            output = {"title": "Bug", "state": "open", "body": "details"}
            resources = (self._issue(),)
        elif invocation.action == "repository.issue.update":
            assert invocation.arguments["state"] == "closed"
            output = {"title": "Bug", "state": "closed", "body": "details"}
            resources = (self._issue(),)
        elif invocation.action == "repository.change_request.open":
            assert invocation.arguments["head_ref"] == "feature"
            assert invocation.arguments["base_ref"] == "main"
            output = {
                "title": "Feature",
                "state": "open",
                "head_ref": "feature",
                "base_ref": "main",
                "body": "change",
            }
            resources = (self._change_request(),)
        elif invocation.action == "repository.change_request.read":
            change_request = invocation.arguments["change_request"]
            assert isinstance(change_request, dict)
            assert change_request["resource_type"] == "repository_change_request"
            output = {
                "title": "Feature",
                "state": "open",
                "head_ref": "feature",
                "base_ref": "main",
                "body": "change",
            }
            resources = (self._change_request(),)
        elif invocation.action == "repository.change_request.update":
            assert invocation.arguments["state"] == "merged"
            output = {
                "title": "Feature",
                "state": "merged",
                "head_ref": "feature",
                "base_ref": "main",
                "body": "change",
            }
            resources = (self._change_request(),)
        else:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"unsupported collaboration action: {invocation.action}",
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
        del connection, resource, context
        raise ContractError(ErrorCode.UNSUPPORTED_CAPABILITY, "file import not supported")

    def _repository(self) -> ExternalResourceReference:
        return ExternalResourceReference(
            id=self.repository_id,
            connection_id=self.connection_id,
            resource_type="repository",
            native_reference=ExternalNativeReference(
                namespace="fake.collaboration",
                native_id="owner/repository",
            ),
            canonical_url="https://forge.invalid/owner/repository",
            revision=_SHA,
        )

    def _issue(self) -> ExternalResourceReference:
        return ExternalResourceReference(
            id=self.issue_id,
            connection_id=self.connection_id,
            resource_type="issue",
            native_reference=ExternalNativeReference(
                namespace="fake.collaboration",
                native_id="owner/repository#17",
            ),
            canonical_url="https://forge.invalid/owner/repository/issues/17",
        )

    def _change_request(self) -> ExternalResourceReference:
        return ExternalResourceReference(
            id=self.change_request_id,
            connection_id=self.connection_id,
            resource_type="merge_request",
            native_reference=ExternalNativeReference(
                namespace="fake.collaboration",
                native_id="owner/repository!9",
            ),
            canonical_url="https://forge.invalid/owner/repository/changes/9",
        )


def _fixture() -> tuple[
    _CollaborationConnector,
    ConnectorRepositoryProvider,
    RepositoryConnection,
    OperationContext,
]:
    project_id = new_id("project")
    actor_ref = new_id("user")
    connection = Connection(
        id=new_id("connection"),
        connector_type_id="fake.collaboration",
        connector_version="1.0",
        owner_type="user",
        owner_id=actor_ref,
        display_name="Collaboration fixture",
        project_id=project_id,
    )
    repository_connection = RepositoryConnection(
        connection=connection,
        provider_id="repository-connector.fake-collaboration",
    )
    connector = _CollaborationConnector(connection.id)
    provider = ConnectorRepositoryProvider(connector, repository_connection)
    context = OperationContext(
        correlation_id="issue-82-collaboration",
        owner_type="user",
        owner_id=actor_ref,
        project_id=project_id,
    )
    return connector, provider, repository_connection, context


def test_connector_repository_collaboration_objects_are_canonical_and_namespaced() -> None:
    async def scenario() -> None:
        connector, provider, connection, context = _fixture()
        repository = (await provider.discover(connection, context))[0]
        capabilities = {
            capability.operation.value: capability for capability in repository.capabilities
        }
        assert capabilities["repository.issue.read"].requires_credentials is True
        assert capabilities["repository.issue.write"].side_effects.value == "external"
        assert capabilities["repository.change_request.read"].requires_credentials is True
        assert capabilities["repository.change_request.write"].side_effects.value == "external"

        issue = await provider.open_issue(repository, "Bug", context, body="details")
        assert issue.external_resource.resource_type == "repository_issue"
        assert issue.external_resource.metadata["provider_resource_type"] == "issue"
        assert issue.external_resource.native_reference.namespace == "fake.collaboration"
        assert issue.state is RepositoryIssueState.OPEN
        assert (
            await provider.read_issue(repository, issue.external_resource, context)
        ).id == issue.id
        closed = await provider.update_issue(
            repository,
            issue.external_resource,
            context,
            state=RepositoryIssueState.CLOSED,
        )
        assert closed.state is RepositoryIssueState.CLOSED

        change_request = await provider.open_change_request(
            repository,
            "Feature",
            "feature",
            "main",
            context,
            body="change",
        )
        assert change_request.external_resource.resource_type == "repository_change_request"
        assert (
            change_request.external_resource.metadata["provider_resource_type"] == "merge_request"
        )
        assert change_request.head_ref == "feature"
        assert change_request.base_ref == "main"
        loaded = await provider.read_change_request(
            repository,
            change_request.external_resource,
            context,
        )
        assert loaded.id == change_request.id
        merged = await provider.update_change_request(
            repository,
            change_request.external_resource,
            context,
            state=RepositoryChangeRequestState.MERGED,
        )
        assert merged.state is RepositoryChangeRequestState.MERGED
        assert connector.actions_seen == [
            "repository.issue.open",
            "repository.issue.read",
            "repository.issue.update",
            "repository.change_request.open",
            "repository.change_request.read",
            "repository.change_request.update",
        ]

    asyncio.run(scenario())


def test_repository_service_denies_collaboration_side_effect_before_connector_call() -> None:
    async def scenario() -> None:
        connector, provider, connection, operation = _fixture()
        repository = (await provider.discover(connection, operation))[0]
        registry = RepositoryRegistry()
        registry.register(RepositoryBinding(connection, repository, provider))
        actor_ref = connection.connection.owner_id
        authorization = AuthorizationGate(
            LocalAuthorizationProvider(
                (
                    LocalPrincipalPolicy(
                        principal_ref=actor_ref,
                        actor_types=frozenset({ActorType.HUMAN}),
                        allowed_actions=frozenset({AuthorizationAction.READ}),
                        resource_types=frozenset({ResourceType.GENERIC}),
                        project_ids=frozenset({connection.connection.project_id}),
                    ),
                )
            )
        )
        service = RepositoryService(registry, authorization)
        call_context = RepositoryCallContext(operation=operation, actor_ref=actor_ref)

        with pytest.raises(ContractError) as issue_error:
            await service.open_issue(repository.id, "Denied", call_context)
        assert issue_error.value.code is ErrorCode.FORBIDDEN

        with pytest.raises(ContractError) as change_error:
            await service.open_change_request(
                repository.id,
                "Denied",
                "feature",
                "main",
                call_context,
            )
        assert change_error.value.code is ErrorCode.FORBIDDEN
        assert connector.actions_seen == []

    asyncio.run(scenario())
