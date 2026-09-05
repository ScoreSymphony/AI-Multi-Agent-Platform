from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.connectors import (
    Connection,
    ConnectionStatus,
    ConnectorRegistry,
    ConnectorService,
    InMemoryConnectorRepository,
)
from ai_multi_agent_platform.connectors.control_plane import register_connector_control_plane
from ai_multi_agent_platform.contracts.types import HealthStatus
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.organizations import (
    InMemoryOrganizationRepository,
    MembershipAuthorizationProvider,
    OrganizationService,
)
from ai_multi_agent_platform.security.authorization import ActorType
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _headers(principal: str) -> dict[str, str]:
    return {
        "X-Principal-Ref": principal,
        "X-Owner-Type": "user",
        "X-Owner-Id": principal.removeprefix("user:"),
    }


async def _stack() -> tuple[ControlPlaneHTTP, OrganizationService, InMemoryConnectorRepository]:
    kernel_repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=kernel_repository,
    )
    organization_repository = InMemoryOrganizationRepository()
    organizations = OrganizationService(organization_repository)
    authorization = MembershipAuthorizationProvider(
        FakeAuthorizationProvider(), organization_repository
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=kernel_repository,
        authorization=authorization,
        organization_service=organizations,
    )
    connector_repository = InMemoryConnectorRepository()
    connectors = ConnectorService(connector_repository, ConnectorRegistry())
    register_connector_control_plane(control_plane, connectors)
    return ControlPlaneHTTP(control_plane), organizations, connector_repository


async def _search(http: ControlPlaneHTTP, principal: str, **query: str) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(method="GET", path="/api/v1/search", headers=_headers(principal), query=query)
    )
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    return response.body


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    items = page["items"]
    assert isinstance(items, list) and all(isinstance(item, dict) for item in items)
    return items


def test_organization_search_and_connection_search_revoke_visibility_live() -> None:
    async def scenario() -> None:
        http, organizations, connector_repository = await _stack()
        organization = await organizations.create_organization(
            name="Search Org", owner_actor_id="user:owner"
        )
        team = await organizations.create_team(organization_id=organization.id, name="Search Team")
        membership = await organizations.add_member(
            actor_id="user:member",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
            team_id=team.id,
        )
        connection = Connection(
            id=new_id("connection"),
            connector_type_id="reference.local",
            connector_version="1.0",
            owner_type="user",
            owner_id="owner",
            display_name="Organization-scoped search connection",
            organization_id=organization.id,
            status=ConnectionStatus.READY,
            health=HealthStatus.HEALTHY,
        )
        await connector_repository.save_connection(connection)

        org_page = await _search(http, "user:member", type="organization", id=organization.id)
        assert org_page["total"] == 1
        team_page = await _search(http, "user:member", type="team", q=organization.id)
        assert {item["resource_id"] for item in _items(team_page)} == {team.id}
        member_page = await _search(http, "user:member", type="membership", q="user:member")
        assert {item["resource_id"] for item in _items(member_page)} == {membership.id}
        conn_page = await _search(http, "user:member", type="connection", id=connection.id)
        assert conn_page["total"] == 1

        for resource_type, resource_id in (
            ("organization", organization.id),
            ("team", team.id),
            ("membership", membership.id),
            ("connection", connection.id),
        ):
            hidden = await _search(http, "user:outsider", type=resource_type, id=resource_id)
            assert hidden["total"] == 0
            assert resource_id not in repr(hidden)

        await organizations.suspend_member(membership.id)
        assert (await _search(http, "user:member", type="organization", id=organization.id))[
            "total"
        ] == 0
        assert (await _search(http, "user:member", type="connection", id=connection.id))[
            "total"
        ] == 0

        owner_member = await _search(http, "user:owner", type="membership", id=membership.id)
        assert owner_member["total"] == 1
        assert _items(owner_member)[0]["status"] == "suspended"

    asyncio.run(scenario())


def test_invitation_redemption_requires_authenticated_identity_binding() -> None:
    async def scenario() -> None:
        _, organizations, _ = await _stack()
        now = datetime(2026, 9, 5, 12, tzinfo=UTC)
        organization = await organizations.create_organization(
            name="Invite Security", owner_actor_id="user:owner", now=now
        )
        email_only = await organizations.invite_member(
            organization_id=organization.id,
            invited_by_actor_id="user:owner",
            intended_email_ref="email-ref:invitee",
            expires_at=now + timedelta(hours=1),
            now=now,
        )
        with pytest.raises(ValueError, match="not bound to an authenticated identity"):
            await organizations.accept_invitation(
                email_only.id, actor_id="user:attacker", now=now + timedelta(minutes=1)
            )
        bound = await organizations.invite_member(
            organization_id=organization.id,
            invited_by_actor_id="user:owner",
            intended_identity_ref="user:invitee",
            intended_email_ref="email-ref:invitee",
            expires_at=now + timedelta(hours=1),
            now=now,
        )
        with pytest.raises(ValueError, match="bound to another identity"):
            await organizations.accept_invitation(
                bound.id, actor_id="user:attacker", now=now + timedelta(minutes=1)
            )
        accepted = await organizations.accept_invitation(
            bound.id, actor_id="user:invitee", now=now + timedelta(minutes=2)
        )
        assert accepted.actor_id == "user:invitee"
        assert accepted.organization_id == organization.id

    asyncio.run(scenario())
