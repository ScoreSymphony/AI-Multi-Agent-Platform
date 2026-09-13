from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.organizations import (
    InMemoryOrganizationRepository,
    InvitationStatus,
    MembershipStatus,
    OrganizationService,
    OrganizationStatus,
    ShareStatus,
)
from ai_multi_agent_platform.security.authorization import ActorType


def test_personal_scope_does_not_require_an_organization() -> None:
    async def scenario() -> None:
        service = OrganizationService(InMemoryOrganizationRepository())
        scope = await service.membership_authorization_scope(actor_id="user:personal")
        assert scope.organization_id is None
        assert scope.team_ids == ()
        identity = await service.actor_identity_for_scope(
            actor_id="user:personal",
            actor_type=ActorType.HUMAN,
        )
        assert identity.organization_id is None
        assert identity.team_ids == ()

    asyncio.run(scenario())


def test_organization_team_membership_and_archive_lifecycle_preserves_records() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=UTC)
        repository = InMemoryOrganizationRepository()
        service = OrganizationService(repository)
        organization = await service.create_organization(
            name="Example",
            owner_actor_id="user:owner",
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
            created_by_actor_id="user:owner",
            now=now,
        )
        scope = await service.membership_authorization_scope(
            actor_id="service:worker",
            organization_id=organization.id,
        )
        assert scope.team_ids == (team.id,)
        assert scope.role_refs == ("role:operator",)
        assert scope.policy_refs == ("policy:worker",)

        archived = await service.archive_organization(
            organization.id,
            now=now + timedelta(hours=1),
        )
        assert archived.status is OrganizationStatus.ARCHIVED
        retained = await repository.get_membership(membership.id)
        assert retained.status is MembershipStatus.REVOKED
        assert retained.revoked_at == now + timedelta(hours=1)
        with pytest.raises(PermissionError):
            await service.actor_identity_for_scope(
                actor_id="service:worker",
                actor_type=ActorType.SERVICE,
                organization_id=organization.id,
            )

    asyncio.run(scenario())


def test_invitation_accept_expire_and_revoke_are_deterministic() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=UTC)
        repository = InMemoryOrganizationRepository()
        service = OrganizationService(repository)
        organization = await service.create_organization(
            name="Example",
            owner_actor_id="user:owner",
            now=now,
        )
        team = await service.create_team(
            organization_id=organization.id,
            name="Reviewers",
            now=now,
        )
        invitation = await service.invite_member(
            organization_id=organization.id,
            team_id=team.id,
            intended_identity_ref="user:reviewer",
            invited_by_actor_id="user:owner",
            expires_at=now + timedelta(hours=2),
            token_ref="secret-ref:invite-1",
            role_refs=("role:reviewer",),
            now=now,
        )
        membership = await service.accept_invitation(
            invitation.id,
            actor_id="user:reviewer",
            now=now + timedelta(minutes=15),
        )
        assert membership.status is MembershipStatus.ACTIVE
        assert membership.team_id == team.id
        assert (await repository.get_invitation(invitation.id)).status is InvitationStatus.ACCEPTED

        expiring = await service.invite_member(
            organization_id=organization.id,
            intended_email_ref="email-ref:late-user",
            invited_by_actor_id="user:owner",
            expires_at=now + timedelta(hours=1),
            token_ref="secret-ref:invite-2",
            now=now,
        )
        with pytest.raises(ValueError, match="expired"):
            await service.accept_invitation(
                expiring.id,
                actor_id="user:late",
                now=now + timedelta(hours=1),
            )
        assert (await repository.get_invitation(expiring.id)).status is InvitationStatus.EXPIRED

        revocable = await service.invite_member(
            organization_id=organization.id,
            intended_email_ref="email-ref:revoked-user",
            invited_by_actor_id="user:owner",
            expires_at=now + timedelta(hours=3),
            token_ref="secret-ref:invite-3",
            now=now,
        )
        revoked = await service.revoke_invitation(
            revocable.id,
            now=now + timedelta(minutes=5),
        )
        assert revoked.status is InvitationStatus.REVOKED

    asyncio.run(scenario())


def test_suspend_remove_and_role_changes_feed_scope_without_becoming_authorization() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=UTC)
        repository = InMemoryOrganizationRepository()
        service = OrganizationService(repository)
        organization = await service.create_organization(
            name="Example",
            owner_actor_id="user:owner",
            now=now,
        )
        member = await service.add_member(
            actor_id="user:member",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
            role_refs=("role:member",),
            created_by_actor_id="user:owner",
            now=now,
        )
        updated = await service.set_membership_assignments(
            member.id,
            role_refs=("role:reviewer",),
            policy_refs=("policy:read",),
        )
        assert updated.role_refs == ("role:reviewer",)
        scope = await service.membership_authorization_scope(
            actor_id="user:member",
            organization_id=organization.id,
        )
        assert scope.role_refs == ("role:reviewer",)
        assert scope.policy_refs == ("policy:read",)

        suspended = await service.suspend_member(
            member.id,
            now=now + timedelta(minutes=1),
        )
        assert suspended.status is MembershipStatus.SUSPENDED
        inactive_scope = await service.membership_authorization_scope(
            actor_id="user:member",
            organization_id=organization.id,
        )
        assert inactive_scope.role_refs == ()

        removed = await service.remove_member(
            member.id,
            now=now + timedelta(minutes=2),
        )
        assert removed.status is MembershipStatus.REVOKED
        assert (await repository.get_membership(member.id)).actor_id == "user:member"

    asyncio.run(scenario())


def test_resource_ownership_sharing_revoke_and_cross_org_isolation() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=UTC)
        repository = InMemoryOrganizationRepository()
        service = OrganizationService(repository)
        org_a = await service.create_organization(
            name="A",
            owner_actor_id="user:owner-a",
            now=now,
        )
        org_b = await service.create_organization(
            name="B",
            owner_actor_id="user:owner-b",
            now=now,
        )
        team_a = await service.create_team(
            organization_id=org_a.id,
            name="A Team",
            now=now,
        )
        team_b = await service.create_team(
            organization_id=org_b.id,
            name="B Team",
            now=now,
        )
        await service.add_member(
            actor_id="user:a",
            actor_type=ActorType.HUMAN,
            organization_id=org_a.id,
            team_id=team_a.id,
            now=now,
        )
        await service.add_member(
            actor_id="user:b",
            actor_type=ActorType.HUMAN,
            organization_id=org_b.id,
            team_id=team_b.id,
            now=now,
        )

        await service.set_resource_owner(
            resource_type="workspace",
            resource_id="workspace_example",
            owner_ref=OwnerRef(type="team", id=team_a.id),
            organization_id=org_a.id,
            created_by_actor_id="user:owner-a",
            now=now,
        )
        assert await service.resource_in_actor_scope(
            actor_id="user:a",
            resource_type="workspace",
            resource_id="workspace_example",
        )
        assert not await service.resource_in_actor_scope(
            actor_id="user:b",
            resource_type="workspace",
            resource_id="workspace_example",
        )

        with pytest.raises(PermissionError, match="cross-organization"):
            await service.share_resource(
                resource_type="workspace",
                resource_id="workspace_example",
                target_ref=OwnerRef(type="team", id=team_b.id),
                granted_by_actor_id="user:owner-a",
                now=now,
            )
        share = await service.share_resource(
            resource_type="workspace",
            resource_id="workspace_example",
            target_ref=OwnerRef(type="team", id=team_b.id),
            granted_by_actor_id="user:owner-a",
            allow_cross_organization=True,
            now=now,
        )
        assert await service.resource_in_actor_scope(
            actor_id="user:b",
            resource_type="workspace",
            resource_id="workspace_example",
        )
        revoked = await service.revoke_share(share.id, now=now + timedelta(minutes=1))
        assert revoked.status is ShareStatus.REVOKED
        assert not await service.resource_in_actor_scope(
            actor_id="user:b",
            resource_type="workspace",
            resource_id="workspace_example",
        )

    asyncio.run(scenario())


def test_external_group_mapping_stays_noncanonical_and_reversible_metadata() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 3, 12, tzinfo=UTC)
        repository = InMemoryOrganizationRepository()
        service = OrganizationService(repository)
        organization = await service.create_organization(
            name="Example",
            owner_actor_id="user:owner",
            now=now,
        )
        team = await service.create_team(
            organization_id=organization.id,
            name="SSO mapped",
            now=now,
        )
        mapping = await service.add_external_group_mapping(
            provider_ref="oidc:example",
            external_group_id="external-group-42",
            organization_id=organization.id,
            team_id=team.id,
            now=now,
        )
        assert mapping.external_group_id == "external-group-42"
        assert mapping.organization_id == organization.id
        assert mapping.team_id == team.id
        assert not organization.id.endswith("external-group-42")
        assert not team.id.endswith("external-group-42")

    asyncio.run(scenario())
