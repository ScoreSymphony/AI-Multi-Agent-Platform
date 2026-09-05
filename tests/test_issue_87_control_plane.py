from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import (
    ORGANIZATION_COLLECTIONS,
    ORGANIZATION_COMMANDS,
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    HTTPResponse,
)
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


def _stack() -> tuple[
    ControlPlane,
    ControlPlaneHTTP,
    OrganizationService,
    FakeAuthorizationProvider,
]:
    kernel_repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=kernel_repository,
    )
    organization_repository = InMemoryOrganizationRepository()
    organizations = OrganizationService(organization_repository)
    canonical_authorization = FakeAuthorizationProvider()
    authorization = MembershipAuthorizationProvider(
        canonical_authorization,
        organization_repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=kernel_repository,
        authorization=authorization,
        organization_service=organizations,
    )
    return (
        control_plane,
        ControlPlaneHTTP(control_plane),
        organizations,
        canonical_authorization,
    )


def _headers(principal: str, *, key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Principal-Ref": principal,
        "X-Owner-Type": "user",
        "X-Owner-Id": principal.removeprefix("user:"),
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


async def _command(
    http: ControlPlaneHTTP,
    command: str,
    resource_ref: str,
    principal: str,
    key: str,
    **payload: JsonValue,
) -> HTTPResponse:
    body: dict[str, JsonValue] = {"resource_ref": resource_ref, **payload}
    return await http.handle(
        HTTPRequest(
            method="POST",
            path=f"/api/v1/commands/{command}",
            headers=_headers(principal, key=key),
            body=body,
        )
    )


def test_organization_resources_and_commands_register_on_current_control_plane() -> None:
    async def scenario() -> None:
        control_plane, http, _, _ = _stack()
        manifest = await http.handle(HTTPRequest(method="GET", path="/api/v1"))
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        assert set(ORGANIZATION_COLLECTIONS).issubset(control_plane.registered_collections)
        assert set(ORGANIZATION_COMMANDS).issubset(control_plane.registered_commands)
        resources = manifest.body["resources"]
        commands = manifest.body["commands"]
        assert isinstance(resources, list)
        assert isinstance(commands, list)
        assert set(ORGANIZATION_COLLECTIONS).issubset(resources)
        assert set(ORGANIZATION_COMMANDS).issubset(commands)

    asyncio.run(scenario())


def test_control_plane_create_team_membership_and_scope_aware_discovery() -> None:
    async def scenario() -> None:
        _, http, _, _ = _stack()
        created = await _command(
            http,
            "organization.create",
            "organizations",
            "user:owner-a",
            "org-create-a",
            name="Org A",
        )
        assert created.status == 200
        assert isinstance(created.body, dict)
        organization_id = created.body["id"]
        assert isinstance(organization_id, str)
        assert created.body["owner_actor_id"] == "user:owner-a"

        team_response = await _command(
            http,
            "team.create",
            organization_id,
            "user:owner-a",
            "team-create-a",
            name="Platform",
        )
        assert team_response.status == 200
        assert isinstance(team_response.body, dict)
        team_id = team_response.body["id"]
        assert isinstance(team_id, str)

        membership_response = await _command(
            http,
            "membership.add",
            organization_id,
            "user:owner-a",
            "member-add-a",
            actor_id="user:member-a",
            actor_type="human",
            team_id=team_id,
            role_refs=["role:member"],
            policy_refs=["policy:team-read"],
        )
        assert membership_response.status == 200

        owner_list = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/organizations",
                headers=_headers("user:owner-a"),
            )
        )
        member_list = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/organizations",
                headers=_headers("user:member-a"),
            )
        )
        outsider_list = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/organizations",
                headers=_headers("user:outsider"),
            )
        )
        for response, total in ((owner_list, 1), (member_list, 1), (outsider_list, 0)):
            assert response.status == 200
            assert isinstance(response.body, dict)
            assert response.body["total"] == total

        hidden_exact = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/organizations/{organization_id}",
                headers=_headers("user:outsider"),
            )
        )
        assert hidden_exact.status == 404

    asyncio.run(scenario())


def test_invitation_projection_hides_token_and_accepts_before_membership_exists() -> None:
    async def scenario() -> None:
        _, http, _, _ = _stack()
        created = await _command(
            http,
            "organization.create",
            "organizations",
            "user:owner",
            "org-create-invite",
            name="Invite Org",
        )
        assert isinstance(created.body, dict)
        organization_id = created.body["id"]
        assert isinstance(organization_id, str)

        expires_at = datetime.now(UTC) + timedelta(hours=1)
        invitation_response = await _command(
            http,
            "invitation.create",
            organization_id,
            "user:owner",
            "invite-create",
            intended_identity_ref="user:invitee",
            expires_at=expires_at.isoformat(),
            token_ref="secret-ref:one-time-invite",
            role_refs=["role:member"],
        )
        assert invitation_response.status == 200
        assert isinstance(invitation_response.body, dict)
        invitation_id = invitation_response.body["id"]
        assert isinstance(invitation_id, str)
        assert "token_ref" not in invitation_response.body
        assert "secret-ref:one-time-invite" not in repr(invitation_response.body)

        invitee_read = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/invitations/{invitation_id}",
                headers=_headers("user:invitee"),
            )
        )
        assert invitee_read.status == 200
        assert "secret-ref:one-time-invite" not in repr(invitee_read.body)

        accepted = await _command(
            http,
            "invitation.accept",
            invitation_id,
            "user:invitee",
            "invite-accept",
        )
        assert accepted.status == 200
        assert isinstance(accepted.body, dict)
        assert accepted.body["actor_id"] == "user:invitee"
        assert accepted.body["status"] == "active"

    asyncio.run(scenario())


def test_suspended_member_loses_discovery_and_future_team_mutation() -> None:
    async def scenario() -> None:
        _, http, organizations, canonical = _stack()
        organization = await organizations.create_organization(
            name="Revocation Org",
            owner_actor_id="user:owner",
        )
        team = await organizations.create_team(
            organization_id=organization.id,
            name="Before",
        )
        membership = await organizations.add_member(
            actor_id="user:member",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
            team_id=team.id,
        )

        before = await _command(
            http,
            "team.update",
            team.id,
            "user:member",
            "team-update-before",
            name="Allowed while active",
        )
        assert before.status == 200

        await organizations.suspend_member(membership.id)
        listed = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/organizations",
                headers=_headers("user:member"),
            )
        )
        assert listed.status == 200
        assert isinstance(listed.body, dict)
        assert listed.body["total"] == 0

        denied = await _command(
            http,
            "team.update",
            team.id,
            "user:member",
            "team-update-after",
            name="Must not apply",
        )
        assert denied.status == 403
        assert (await organizations.repository.get_team(team.id)).name == "Allowed while active"
        assert any(call.action == "team.update" for call in canonical.calls)

    asyncio.run(scenario())
