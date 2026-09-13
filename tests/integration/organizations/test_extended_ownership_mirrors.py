from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import ControlPlane, RequestContext
from ai_multi_agent_platform.control_plane.models import ActorContext, OwnerType
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.organizations import (
    InMemoryOrganizationRepository,
    OrganizationService,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _stack() -> tuple[ControlPlane, OrganizationService, InMemoryOrganizationRepository]:
    kernel_repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=kernel_repository,
    )
    organization_repository = InMemoryOrganizationRepository()
    organizations = OrganizationService(organization_repository)
    control_plane = ControlPlane(
        kernel=kernel,
        events=kernel_repository,
        authorization=FakeAuthorizationProvider(),
        organization_service=organizations,
    )
    return control_plane, organizations, organization_repository


def _context(
    principal: str,
    *,
    owner_type: OwnerType,
    owner_id: str,
    key: str,
) -> RequestContext:
    return RequestContext(
        request_id=f"request-{key}",
        correlation_id=f"correlation-{key}",
        actor=ActorContext(
            principal_ref=principal,
            owner_type=owner_type,
            owner_id=owner_id,
        ),
        idempotency_key=key,
    )


def test_agent_and_agent_team_commands_mirror_authoritative_owner_changes() -> None:
    async def scenario() -> None:
        control_plane, organizations, repository = _stack()
        organization = await organizations.create_organization(
            name="Owner Org",
            owner_actor_id="user:owner",
        )
        team = await organizations.create_team(
            organization_id=organization.id,
            name="Agent Team",
        )
        state = {"agent_owner": OwnerRef(type="team", id=team.id)}

        async def create_agent(
            context: RequestContext,
            resource_ref: str,
            payload: dict[str, JsonValue],
        ) -> dict[str, JsonValue]:
            del context, resource_ref, payload
            owner = state["agent_owner"]
            return {
                "id": "agent_test-owner-mirror",
                "type": "agent",
                "owner_ref": {"type": owner.type, "id": owner.id},
            }

        async def update_agent(
            context: RequestContext,
            resource_ref: str,
            payload: dict[str, JsonValue],
        ) -> dict[str, JsonValue]:
            del context, resource_ref, payload
            owner = state["agent_owner"]
            return {
                "id": "agent_test-owner-mirror",
                "type": "agent",
                "owner_ref": {"type": owner.type, "id": owner.id},
            }

        async def create_agent_team(
            context: RequestContext,
            resource_ref: str,
            payload: dict[str, JsonValue],
        ) -> dict[str, JsonValue]:
            del context, resource_ref, payload
            return {
                "id": "agent_team_test-owner-mirror",
                "type": "agent_team",
                "owner_ref": {"type": "organization", "id": organization.id},
            }

        control_plane.register_command("agent.create", create_agent)
        control_plane.register_command("agent.update", update_agent)
        control_plane.register_command("agent-team.create", create_agent_team)
        context = _context(
            "user:owner",
            owner_type="organization",
            owner_id=organization.id,
            key="agent-mirror",
        )

        await control_plane.execute_command(context, "agent.create", "agents", {})
        original = await repository.get_ownership("agent", "agent_test-owner-mirror")
        assert original.owner_ref == OwnerRef(type="team", id=team.id)
        assert original.organization_id == organization.id

        state["agent_owner"] = OwnerRef(type="organization", id=organization.id)
        await control_plane.execute_command(
            _context(
                "user:owner",
                owner_type="organization",
                owner_id=organization.id,
                key="agent-owner-update",
            ),
            "agent.update",
            "agent_test-owner-mirror",
            {},
        )
        updated = await repository.get_ownership("agent", "agent_test-owner-mirror")
        assert updated.id == original.id
        assert updated.owner_ref == OwnerRef(type="organization", id=organization.id)
        assert updated.organization_id == organization.id

        await control_plane.execute_command(
            _context(
                "user:owner",
                owner_type="organization",
                owner_id=organization.id,
                key="agent-team-mirror",
            ),
            "agent-team.create",
            "agent-teams",
            {},
        )
        team_ownership = await repository.get_ownership(
            "agent_team", "agent_team_test-owner-mirror"
        )
        assert team_ownership.owner_ref == OwnerRef(type="organization", id=organization.id)

    asyncio.run(scenario())


def test_automation_create_mirrors_identity_owner() -> None:
    async def scenario() -> None:
        control_plane, organizations, repository = _stack()
        organization = await organizations.create_organization(
            name="Automation Org",
            owner_actor_id="user:owner",
        )
        context = _context(
            "user:owner",
            owner_type="organization",
            owner_id=organization.id,
            key="automation-mirror",
        )
        automation = await control_plane.execute_command(
            context,
            "automation.create",
            "automations",
            {
                "name": "Organization automation",
                "trigger": {"type": "manual"},
                "task_template": {
                    "title": "Run organization task",
                    "objective": "Exercise canonical ownership mirroring",
                },
            },
        )
        automation_id = automation["id"]
        assert isinstance(automation_id, str)
        ownership = await repository.get_ownership("automation", automation_id)
        assert ownership.owner_ref == OwnerRef(type="organization", id=organization.id)
        assert ownership.organization_id == organization.id

    asyncio.run(scenario())


def test_connection_commands_strictly_mirror_structured_canonical_owner() -> None:
    async def scenario() -> None:
        control_plane, organizations, repository = _stack()
        organization = await organizations.create_organization(
            name="Connection Org",
            owner_actor_id="user:owner",
        )
        connection_id = "connection_test-owner-mirror"

        async def connection_resource(
            context: RequestContext,
            resource_ref: str,
            payload: dict[str, JsonValue],
        ) -> dict[str, JsonValue]:
            del context, resource_ref, payload
            return {
                "id": connection_id,
                "type": "connection",
                "owner_type": "organization",
                "owner_id": organization.id,
                "organization_id": organization.id,
            }

        for command in (
            "connection.create",
            "connection.enable",
            "connection.disable",
            "connection.health",
        ):
            control_plane.register_command(command, connection_resource)

        context = _context(
            "user:owner",
            owner_type="organization",
            owner_id=organization.id,
            key="connection-create",
        )
        await control_plane.execute_command(context, "connection.create", "connections", {})
        ownership = await repository.get_ownership("connection", connection_id)
        assert ownership.owner_ref == OwnerRef(type="organization", id=organization.id)
        assert ownership.organization_id == organization.id

        await control_plane.execute_command(
            _context(
                "user:owner",
                owner_type="organization",
                owner_id=organization.id,
                key="connection-enable",
            ),
            "connection.enable",
            connection_id,
            {},
        )
        replay = await repository.get_ownership("connection", connection_id)
        assert replay.id == ownership.id
        assert replay.owner_ref == ownership.owner_ref

        with pytest.raises(ContractError) as direct_transfer:
            await control_plane.execute_command(
                context,
                "resource-ownership.transfer",
                connection_id,
                {
                    "resource_type": "connection",
                    "resource_id": connection_id,
                    "owner_ref": {"type": "user", "id": "other"},
                },
            )
        assert direct_transfer.value.code is ErrorCode.CONFLICT

    asyncio.run(scenario())
