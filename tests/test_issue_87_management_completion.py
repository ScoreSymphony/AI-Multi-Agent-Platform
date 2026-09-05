from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ControlPlane, RequestContext
from ai_multi_agent_platform.control_plane.models import ActorContext, OwnerType
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.organizations import (
    InMemoryOrganizationRepository,
    MembershipStatus,
    OrganizationService,
)
from ai_multi_agent_platform.security.authorization import ActorType
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeEventProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _stack() -> tuple[
    ControlPlane,
    OrganizationService,
    InMemoryOrganizationRepository,
]:
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
        organization_audit_events=FakeEventProvider(),
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


def test_organization_and_team_configuration_are_canonical_and_audited() -> None:
    async def scenario() -> None:
        control_plane, organizations, repository = _stack()
        organization = await organizations.create_organization(
            name="Before",
            owner_actor_id="user:owner",
        )
        team = await organizations.create_team(
            organization_id=organization.id,
            name="Before Team",
        )
        original_org_updated_at = organization.updated_at
        original_team_updated_at = team.updated_at
        context = _context(
            "user:owner",
            owner_type="organization",
            owner_id=organization.id,
            key="organization-update",
        )

        updated_org = await control_plane.execute_command(
            context,
            "organization.update",
            organization.id,
            {
                "name": "After",
                "display_name": "After Display",
                "administrator_actor_ids": ["user:admin"],
                "settings": {"region": "eu"},
                "default_policy_refs": ["policy:organization-default"],
                "default_configuration_refs": ["config:organization-default"],
            },
        )
        assert updated_org["name"] == "After"
        stored_org = await repository.get_organization(organization.id)
        assert stored_org.display_name == "After Display"
        assert stored_org.administrator_actor_ids == ("user:admin",)
        assert stored_org.settings == {"region": "eu"}
        assert stored_org.default_policy_refs == ("policy:organization-default",)
        assert stored_org.default_configuration_refs == ("config:organization-default",)
        assert stored_org.updated_at >= original_org_updated_at

        configured_team = await control_plane.execute_command(
            _context(
                "user:owner",
                owner_type="organization",
                owner_id=organization.id,
                key="team-configure",
            ),
            "team.configure",
            team.id,
            {
                "name": "After Team",
                "description": "Scoped team",
                "project_scope_refs": ["project_scope:alpha"],
                "default_policy_refs": ["policy:team-default"],
                "default_configuration_refs": ["config:team-default"],
            },
        )
        assert configured_team["name"] == "After Team"
        stored_team = await repository.get_team(team.id)
        assert stored_team.description == "Scoped team"
        assert stored_team.project_scope_refs == ("project_scope:alpha",)
        assert stored_team.default_policy_refs == ("policy:team-default",)
        assert stored_team.default_configuration_refs == ("config:team-default",)
        assert stored_team.updated_at >= original_team_updated_at

        assert control_plane.organization_audit is not None
        history = await control_plane.organization_audit.read_organization_history(organization.id)
        assert [event.event_type for event in history] == [
            "organization.update",
            "team.configure",
        ]
        assert all(
            event.provenance is not None and event.provenance.actor_ref == "user:owner"
            for event in history
        )

    asyncio.run(scenario())


def test_organization_owner_transfer_requires_current_owner_and_active_target() -> None:
    async def scenario() -> None:
        control_plane, organizations, repository = _stack()
        organization = await organizations.create_organization(
            name="Transfer Org",
            owner_actor_id="user:owner",
            administrator_actor_ids=("user:admin",),
        )
        owner_membership = await organizations.add_member(
            actor_id="user:owner",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
        )
        await organizations.add_member(
            actor_id="user:admin",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
        )

        with pytest.raises(ContractError) as admin_error:
            await control_plane.execute_command(
                _context(
                    "user:admin",
                    owner_type="organization",
                    owner_id=organization.id,
                    key="owner-transfer-admin",
                ),
                "organization.owner.transfer",
                organization.id,
                {"new_owner_actor_id": "user:admin"},
            )
        assert admin_error.value.code is ErrorCode.FORBIDDEN
        assert (await repository.get_organization(organization.id)).owner_actor_id == "user:owner"

        with pytest.raises(ValueError, match="active membership"):
            await control_plane.execute_command(
                _context(
                    "user:owner",
                    owner_type="organization",
                    owner_id=organization.id,
                    key="owner-transfer-missing-member",
                ),
                "organization.owner.transfer",
                organization.id,
                {"new_owner_actor_id": "user:not-a-member"},
            )

        transferred = await control_plane.execute_command(
            _context(
                "user:owner",
                owner_type="organization",
                owner_id=organization.id,
                key="owner-transfer",
            ),
            "organization.owner.transfer",
            organization.id,
            {"new_owner_actor_id": "user:admin"},
        )
        assert transferred["owner_actor_id"] == "user:admin"
        assert (await repository.get_organization(organization.id)).owner_actor_id == "user:admin"

        left = await control_plane.execute_command(
            _context(
                "user:owner",
                owner_type="user",
                owner_id="owner",
                key="former-owner-leave",
            ),
            "membership.leave",
            owner_membership.id,
            {},
        )
        assert left["status"] == MembershipStatus.LEFT.value
        assert (await repository.get_membership(owner_membership.id)).status is MembershipStatus.LEFT

    asyncio.run(scenario())


def test_membership_leave_is_self_service_and_preserves_relationship_history() -> None:
    async def scenario() -> None:
        control_plane, organizations, repository = _stack()
        organization = await organizations.create_organization(
            name="Leave Org",
            owner_actor_id="user:owner",
        )
        membership = await organizations.add_member(
            actor_id="user:member",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
        )

        left = await control_plane.execute_command(
            _context(
                "user:member",
                owner_type="user",
                owner_id="member",
                key="membership-leave",
            ),
            "membership.leave",
            membership.id,
            {},
        )
        assert left["status"] == MembershipStatus.LEFT.value
        stored = await repository.get_membership(membership.id)
        assert stored.status is MembershipStatus.LEFT
        assert stored.actor_id == "user:member"
        assert stored.revoked_at is not None

        other_membership = await organizations.add_member(
            actor_id="user:other-target",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
        )
        with pytest.raises(ValueError, match="only leave its own membership"):
            await control_plane.execute_command(
                _context(
                    "user:other",
                    owner_type="user",
                    owner_id="other",
                    key="membership-leave-other",
                ),
                "membership.leave",
                other_membership.id,
                {},
            )

    asyncio.run(scenario())


def test_external_group_mapping_can_be_explicitly_deactivated_and_replayed() -> None:
    async def scenario() -> None:
        control_plane, organizations, repository = _stack()
        organization = await organizations.create_organization(
            name="IdP Org",
            owner_actor_id="user:owner",
        )
        mapping = await organizations.add_external_group_mapping(
            provider_ref="oidc:example",
            external_group_id="external-admins",
            organization_id=organization.id,
        )
        context = _context(
            "user:owner",
            owner_type="organization",
            owner_id=organization.id,
            key="mapping-deactivate",
        )
        result = await control_plane.execute_command(
            context,
            "external-group-mapping.deactivate",
            mapping.id,
            {},
        )
        assert result["active"] is False
        stored = next(
            item
            for item in await repository.list_external_group_mappings()
            if item.id == mapping.id
        )
        assert stored.active is False
        assert stored.external_group_id == "external-admins"
        assert stored.organization_id == organization.id

        replay = await control_plane.execute_command(
            context,
            "external-group-mapping.deactivate",
            mapping.id,
            {},
        )
        assert replay == result

    asyncio.run(scenario())


def test_archived_organization_rejects_management_update() -> None:
    async def scenario() -> None:
        control_plane, organizations, _ = _stack()
        organization = await organizations.create_organization(
            name="Archived",
            owner_actor_id="user:owner",
        )
        await organizations.archive_organization(organization.id)
        with pytest.raises(ValueError, match="archived organizations cannot be updated"):
            await control_plane.execute_command(
                _context(
                    "user:owner",
                    owner_type="organization",
                    owner_id=organization.id,
                    key="archived-org-update",
                ),
                "organization.update",
                organization.id,
                {"name": "Should not change"},
            )

    asyncio.run(scenario())
