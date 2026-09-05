from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.configuration.secrets import LocalSecretProvider
from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectionStatus,
    ConnectorRegistry,
    ConnectorService,
    ExternalNativeReference,
    ExternalResourceReference,
    InMemoryConnectorRepository,
    ReferenceConnectorProvider,
    SyncMode,
)
from ai_multi_agent_platform.connectors.control_plane import register_connector_control_plane
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    AuthorizationDecision,
    AuthorizationRequest,
    HealthStatus,
    OperationContext,
)
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.organizations import (
    InMemoryOrganizationRepository,
    MembershipAuthorizationProvider,
    OrganizationService,
)
from ai_multi_agent_platform.security import ActorIdentity, ActorType, SecretReference
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)

PRIVATE_METADATA = "private-remote-payload-must-not-be-searchable"
PRIVATE_PROVENANCE = "private-provider-provenance-must-not-be-searchable"
PRIVATE_URL_TOKEN = "private-url-token-must-not-be-searchable"


class ScopedExternalResourceAuthorization(FakeAuthorizationProvider):
    def __init__(self, denied_project_id: str) -> None:
        super().__init__()
        self.denied_project_id = denied_project_id

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if (
            request.action
            in {
                "external-resource:list",
                "external-resource:read",
                "external-resource.detach",
            }
            and request.context.project_id == self.denied_project_id
        ):
            return AuthorizationDecision(allowed=False, reason="external-resource-project-hidden")
        return AuthorizationDecision(allowed=True, reason="external-resource-visible")


def _headers(
    principal: str = "user:external-resource-test",
    *,
    key: str | None = None,
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-issue-292",
        "X-Correlation-Id": "correlation-issue-292",
        "X-Principal-Ref": principal,
        "X-Owner-Type": "user",
        "X-Owner-Id": principal.removeprefix("user:"),
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _operation_context(project_id: str) -> OperationContext:
    return OperationContext(
        correlation_id="issue-292-service",
        owner_type="user",
        owner_id="external-resource-test",
        project_id=project_id,
    )


async def _base_stack(
    authorization: FakeAuthorizationProvider | None = None,
) -> tuple[
    ControlPlane,
    ControlPlaneHTTP,
    LocalSecretProvider,
    ConnectorService,
    InMemoryConnectorRepository,
]:
    kernel_repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=kernel_repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=kernel_repository,
        authorization=authorization,
    )
    secrets = LocalSecretProvider()
    connector_repository = InMemoryConnectorRepository()
    connectors = ConnectorService(connector_repository, ConnectorRegistry())
    await connectors.register_provider(ReferenceConnectorProvider(secrets))
    register_connector_control_plane(control_plane, connectors)
    return control_plane, ControlPlaneHTTP(control_plane), secrets, connectors, connector_repository


async def _create_connection(
    http: ControlPlaneHTTP,
    secrets: LocalSecretProvider,
    *,
    project_id: str,
    key: str,
) -> str:
    secret_ref = SecretReference(
        provider="local-secrets",
        secret_id=f"issue-292-{key}",
        scope=project_id,
    )
    await secrets.create(
        secret_ref,
        f"secret-material-{key}",
        purpose="connector-auth",
        allowed_consumers=("connector.reference",),
        allowed_purposes=("connector-auth",),
    )
    response = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/commands/connection.create",
            headers=_headers(key=key),
            body={
                "resource_ref": "connections",
                "connector_type_id": "reference.local",
                "connector_version": "1.0",
                "owner_type": "user",
                "owner_id": "external-resource-test",
                "display_name": "Issue 292 reference connection",
                "project_id": project_id,
                "secret_references": [secret_ref.to_dict()],
                "requested_scopes": ["read", "write"],
            },
        )
    )
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    connection_id = response.body["id"]
    assert isinstance(connection_id, str)
    return connection_id


async def _search(
    http: ControlPlaneHTTP,
    principal: str = "user:external-resource-test",
    **query: str,
) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(
            method="GET",
            path="/api/v1/search",
            headers=_headers(principal),
            query=query,
        )
    )
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    return response.body


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    items = page["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return items


def _connection(
    *,
    project_id: str,
    owner_id: str,
    organization_id: str | None = None,
) -> Connection:
    return Connection(
        id=new_id("connection"),
        connector_type_id="reference.local",
        connector_version="1.0",
        owner_type="user",
        owner_id=owner_id,
        display_name=f"Connection {owner_id}",
        project_id=project_id,
        organization_id=organization_id,
        status=ConnectionStatus.READY,
        health=HealthStatus.HEALTHY,
    )


def _reference(
    connection_id: str,
    native_id: str,
    *,
    metadata: dict[str, str] | None = None,
    provenance: dict[str, str] | None = None,
    canonical_url: str | None = None,
) -> ExternalResourceReference:
    return ExternalResourceReference(
        id=new_id("external_resource"),
        connection_id=connection_id,
        resource_type="record",
        native_reference=ExternalNativeReference(
            namespace="provider.records",
            native_id=native_id,
        ),
        canonical_url=canonical_url,
        version="v1",
        revision="r1",
        metadata=metadata or {},
        provenance=provenance or {},
    )


def test_sync_is_the_durable_boundary_and_search_uses_only_persisted_safe_wrappers() -> None:
    async def scenario() -> None:
        control_plane, http, secrets, connectors, repository = await _base_stack()
        project_id = new_id("project")
        connection_id = await _create_connection(
            http,
            secrets,
            project_id=project_id,
            key="create-sync-boundary",
        )
        actor = ActorIdentity("user:external-resource-test", ActorType.HUMAN)
        context = _operation_context(project_id)

        transient = await connectors.list_resources(
            connection_id,
            "record",
            actor=actor,
            context=context,
        )
        assert len(transient) == 2
        assert await repository.list_external_resources(connection_id=connection_id) == ()
        assert (await _search(http, type="external-resource"))["total"] == 0

        synced = await connectors.synchronize(
            connection_id,
            "records",
            actor=actor,
            context=context,
        )
        assert len(synced.resources) == 2
        durable = await repository.list_external_resources(connection_id=connection_id)
        assert {item.id for item in durable} == {item.id for item in synced.resources}

        listed = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/external-resources",
                headers=_headers(),
                query={"filter[connection_id]": connection_id},
            )
        )
        assert listed.status == 200, listed.body
        assert isinstance(listed.body, dict)
        assert listed.body["total"] == 2
        first = listed.body["items"][0]
        assert isinstance(first, dict)
        assert first["id"].startswith("external_resource_")
        assert first["native_reference"]["native_id"] in {"alpha", "beta"}
        assert "metadata" not in first
        assert "provenance" not in first

        resource_id = durable[0].id
        exact_read = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/external-resources/{resource_id}",
                headers=_headers(),
            )
        )
        assert exact_read.status == 200
        assert isinstance(exact_read.body, dict)
        assert exact_read.body["id"] == resource_id

        exact_search = await _search(http, type="external-resource", id=resource_id)
        assert exact_search["total"] == 1
        exact_item = _items(exact_search)[0]
        assert exact_item["resource_id"] == resource_id
        assert exact_item["canonical_ref"] == f"/api/v1/external-resources/{resource_id}"

        native_search = await _search(
            http,
            type="external-resource",
            q=durable[0].native_reference.native_id,
        )
        assert native_search["total"] == 1
        assert _items(native_search)[0]["resource_id"] == resource_id
        assert _items(native_search)[0]["resource_id"] != durable[0].native_reference.native_id

        private = _reference(
            connection_id,
            "safe-native-private",
            metadata={"payload": PRIVATE_METADATA},
            provenance={"provider_private": PRIVATE_PROVENANCE},
            canonical_url=f"https://example.invalid/object?token={PRIVATE_URL_TOKEN}",
        )
        await repository.save_external_resource(private)
        private_read = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/external-resources/{private.id}",
                headers=_headers(),
            )
        )
        assert private_read.status == 200
        assert isinstance(private_read.body, dict)
        assert private_read.body["canonical_url"] is None
        assert PRIVATE_METADATA not in repr(private_read.body)
        assert PRIVATE_PROVENANCE not in repr(private_read.body)
        assert PRIVATE_URL_TOKEN not in repr(private_read.body)

        await control_plane.rebuild_search_index()
        by_native = await _search(http, type="external-resource", q="safe-native-private")
        assert by_native["total"] == 1
        for forbidden in (PRIVATE_METADATA, PRIVATE_PROVENANCE, PRIVATE_URL_TOKEN):
            leaked = await _search(http, q=forbidden)
            assert leaked["total"] == 0

    asyncio.run(scenario())


def test_detach_rebuild_and_connection_removal_delete_stale_derived_search_state() -> None:
    async def scenario() -> None:
        control_plane, http, secrets, connectors, repository = await _base_stack()
        project_id = new_id("project")
        connection_id = await _create_connection(
            http,
            secrets,
            project_id=project_id,
            key="create-lifecycle",
        )
        actor = ActorIdentity("user:external-resource-test", ActorType.HUMAN)
        context = _operation_context(project_id)
        synced = await connectors.synchronize(
            connection_id,
            "records",
            actor=actor,
            context=context,
        )
        detached_id = synced.resources[0].id

        detached = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/external-resource.detach",
                headers=_headers(key="detach-resource"),
                body={"resource_ref": detached_id},
            )
        )
        assert detached.status == 200, detached.body
        assert detached.body == {
            "id": detached_id,
            "detached": True,
            "remote_deleted": False,
        }
        with pytest.raises(ContractError) as detached_missing:
            await repository.get_external_resource(detached_id)
        assert detached_missing.value.code is ErrorCode.NOT_FOUND
        assert (await _search(http, type="external-resource", id=detached_id))["total"] == 0

        stale = _reference(connection_id, "stale-before-rebuild")
        await repository.save_external_resource(stale)
        assert (await _search(http, type="external-resource", id=stale.id))["total"] == 1
        rebuilt = await connectors.synchronize(
            connection_id,
            "records",
            actor=actor,
            context=context,
            mode=SyncMode.REBUILD,
        )
        assert len(rebuilt.resources) == 2
        with pytest.raises(ContractError) as stale_missing:
            await repository.get_external_resource(stale.id)
        assert stale_missing.value.code is ErrorCode.NOT_FOUND
        await control_plane.rebuild_search_index()
        assert (await _search(http, type="external-resource", id=stale.id))["total"] == 0

        remaining_ids = {
            item.id for item in await repository.list_external_resources(connection_id=connection_id)
        }
        assert remaining_ids
        removed = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/connection.remove",
                headers=_headers(key="remove-connection"),
                body={"resource_ref": connection_id},
            )
        )
        assert removed.status == 200, removed.body
        assert await repository.list_external_resources(connection_id=connection_id) == ()
        await control_plane.rebuild_search_index()
        for resource_id in remaining_ids:
            assert (await _search(http, type="external-resource", id=resource_id))["total"] == 0

    asyncio.run(scenario())


def test_project_authorization_filters_direct_counts_exact_ids_and_search() -> None:
    async def scenario() -> None:
        visible_project = new_id("project")
        hidden_project = new_id("project")
        authorization = ScopedExternalResourceAuthorization(hidden_project)
        control_plane, http, _, _, repository = await _base_stack(authorization)
        visible_connection = _connection(
            project_id=visible_project,
            owner_id="visible-owner",
        )
        hidden_connection = _connection(
            project_id=hidden_project,
            owner_id="hidden-owner",
        )
        await repository.save_connection(visible_connection)
        await repository.save_connection(hidden_connection)
        visible = _reference(visible_connection.id, "visible-native")
        hidden = _reference(hidden_connection.id, "hidden-native")
        await repository.save_external_resource(visible)
        await repository.save_external_resource(hidden)

        direct = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/external-resources",
                headers=_headers(),
            )
        )
        assert direct.status == 200
        assert isinstance(direct.body, dict)
        assert direct.body["total"] == 1
        assert visible.id in repr(direct.body)
        assert hidden.id not in repr(direct.body)

        hidden_get = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/external-resources/{hidden.id}",
                headers=_headers(),
            )
        )
        assert hidden_get.status == 404
        assert hidden.id not in repr(hidden_get.body)

        await control_plane.rebuild_search_index()
        search_page = await _search(http, type="external-resource")
        assert search_page["total"] == 1
        assert visible.id in repr(search_page)
        assert hidden.id not in repr(search_page)
        hidden_exact = await _search(http, type="external-resource", id=hidden.id)
        assert hidden_exact["total"] == 0
        assert hidden.id not in repr(hidden_exact)
        assert any(
            call.action == "external-resource:list"
            and call.context.project_id == hidden_project
            for call in authorization.calls
        )

    asyncio.run(scenario())


def test_organization_membership_controls_direct_and_search_visibility_live() -> None:
    async def scenario() -> None:
        kernel_repository = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=kernel_repository,
        )
        organization_repository = InMemoryOrganizationRepository()
        organizations = OrganizationService(organization_repository)
        authorization = MembershipAuthorizationProvider(
            FakeAuthorizationProvider(),
            organization_repository,
        )
        control_plane = ControlPlane(
            kernel=kernel,
            events=kernel_repository,
            authorization=authorization,
            organization_service=organizations,
        )
        repository = InMemoryConnectorRepository()
        connectors = ConnectorService(repository, ConnectorRegistry())
        register_connector_control_plane(control_plane, connectors)
        http = ControlPlaneHTTP(control_plane)

        organization = await organizations.create_organization(
            name="Issue 292 Organization",
            owner_actor_id="user:org-owner",
        )
        membership = await organizations.add_member(
            actor_id="user:org-member",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
        )
        connection = _connection(
            project_id=new_id("project"),
            owner_id="org-owner",
            organization_id=organization.id,
        )
        await repository.save_connection(connection)
        resource = _reference(connection.id, "organization-native")
        await repository.save_external_resource(resource)

        member_direct = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/external-resources",
                headers=_headers("user:org-member"),
            )
        )
        assert member_direct.status == 200
        assert isinstance(member_direct.body, dict)
        assert member_direct.body["total"] == 1
        outsider_direct = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/external-resources",
                headers=_headers("user:outsider"),
            )
        )
        assert outsider_direct.status == 200
        assert isinstance(outsider_direct.body, dict)
        assert outsider_direct.body["total"] == 0
        assert resource.id not in repr(outsider_direct.body)

        assert (
            await _search(
                http,
                "user:org-member",
                type="external-resource",
                id=resource.id,
            )
        )["total"] == 1
        outsider_search = await _search(
            http,
            "user:outsider",
            type="external-resource",
            id=resource.id,
        )
        assert outsider_search["total"] == 0
        assert resource.id not in repr(outsider_search)

        await organizations.suspend_member(membership.id)
        suspended_direct = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/external-resources",
                headers=_headers("user:org-member"),
            )
        )
        assert suspended_direct.status == 200
        assert isinstance(suspended_direct.body, dict)
        assert suspended_direct.body["total"] == 0
        assert (
            await _search(
                http,
                "user:org-member",
                type="external-resource",
                id=resource.id,
            )
        )["total"] == 0

    asyncio.run(scenario())
