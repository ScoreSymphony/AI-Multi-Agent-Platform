from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.organizations import (
    OrganizationService,
    SqliteOrganizationRepository,
)
from ai_multi_agent_platform.security.authorization import ActorType


def test_sqlite_organization_repository_survives_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=UTC)
        db_path = tmp_path / "organizations.sqlite3"
        repository = SqliteOrganizationRepository(db_path)
        service = OrganizationService(repository)

        organization = await service.create_organization(
            name="Durable Org",
            display_name="Durable Organization",
            owner_actor_id="user:owner",
            administrator_actor_ids=("user:admin",),
            now=now,
        )
        team = await service.create_team(
            organization_id=organization.id,
            name="Platform",
            now=now,
        )
        membership = await service.add_member(
            actor_id="service:worker",
            actor_type=ActorType.SERVICE,
            organization_id=organization.id,
            team_id=team.id,
            role_refs=("role:operator",),
            policy_refs=("policy:worker",),
            now=now,
        )
        invitation = await service.invite_member(
            organization_id=organization.id,
            team_id=team.id,
            intended_identity_ref="user:invitee",
            invited_by_actor_id="user:owner",
            expires_at=now + timedelta(hours=2),
            token_ref="secret-ref:durable-invite",
            now=now,
        )
        ownership = await service.set_resource_owner(
            resource_type="workspace",
            resource_id="workspace_durable",
            owner_ref=OwnerRef(type="team", id=team.id),
            organization_id=organization.id,
            created_by_actor_id="user:owner",
            now=now,
        )
        share = await service.share_resource(
            resource_type="workspace",
            resource_id="workspace_durable",
            target_ref=OwnerRef(type="user", id="user:collaborator"),
            granted_by_actor_id="user:owner",
            policy_refs=("policy:read",),
            now=now,
        )
        mapping = await service.add_external_group_mapping(
            provider_ref="oidc:example",
            external_group_id="group-durable",
            organization_id=organization.id,
            team_id=team.id,
            now=now,
        )

        restarted = SqliteOrganizationRepository(db_path)
        assert (await restarted.get_organization(organization.id)) == organization
        assert (await restarted.get_team(team.id)) == team
        assert (await restarted.get_membership(membership.id)) == membership
        assert (await restarted.get_invitation(invitation.id)) == invitation
        assert (await restarted.get_ownership("workspace", "workspace_durable")) == ownership
        assert (await restarted.get_share(share.id)) == share
        assert await restarted.list_external_group_mappings(organization.id) == (mapping,)
        assert await restarted.list_ownerships() == (ownership,)
        assert await restarted.list_all_shares() == (share,)

    asyncio.run(scenario())


def test_sqlite_membership_updates_replace_record_without_erasing_identity(tmp_path: Path) -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=UTC)
        db_path = tmp_path / "memberships.sqlite3"
        repository = SqliteOrganizationRepository(db_path)
        service = OrganizationService(repository)
        organization = await service.create_organization(
            name="Example",
            owner_actor_id="user:owner",
            now=now,
        )
        membership = await service.add_member(
            actor_id="user:member",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
            role_refs=("role:member",),
            now=now,
        )
        suspended = await service.suspend_member(
            membership.id,
            now=now + timedelta(minutes=1),
        )
        removed = await service.remove_member(
            membership.id,
            now=now + timedelta(minutes=2),
        )

        restarted = SqliteOrganizationRepository(db_path)
        persisted = await restarted.get_membership(membership.id)
        assert persisted == removed
        assert persisted.id == membership.id == suspended.id
        assert persisted.actor_id == "user:member"
        assert persisted.created_at == membership.created_at
        assert len(await restarted.list_memberships(actor_id="user:member")) == 1

    asyncio.run(scenario())
