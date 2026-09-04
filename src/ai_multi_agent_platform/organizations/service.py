"""Organization/team/membership lifecycle and ownership semantics for issue #87."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security.authorization import ActorIdentity, ActorType

from .models import (
    ExternalGroupMapping,
    Invitation,
    InvitationStatus,
    Membership,
    MembershipAuthorizationScope,
    MembershipStatus,
    Organization,
    OrganizationStatus,
    ResourceOwnership,
    ResourceShare,
    ShareStatus,
    Team,
    TeamStatus,
    require_aware,
    utc_now,
)
from .repository import OrganizationRepository


class OrganizationService:
    """Owns organizational relationships but never makes final #15 authorization decisions."""

    def __init__(self, repository: OrganizationRepository) -> None:
        self._repository = repository

    @property
    def repository(self) -> OrganizationRepository:
        return self._repository

    async def create_organization(
        self,
        *,
        name: str,
        owner_actor_id: str,
        display_name: str | None = None,
        administrator_actor_ids: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> Organization:
        current = require_aware(now or utc_now(), "now")
        organization = Organization(
            name=name,
            display_name=display_name,
            owner_actor_id=owner_actor_id,
            administrator_actor_ids=administrator_actor_ids,
            created_at=current,
            updated_at=current,
        )
        return await self._repository.save_organization(organization)

    async def archive_organization(
        self,
        organization_id: str,
        *,
        now: datetime | None = None,
    ) -> Organization:
        organization = await self._repository.get_organization(organization_id)
        if organization.status is OrganizationStatus.ARCHIVED:
            return organization
        current = require_aware(now or utc_now(), "now")
        archived = replace(
            organization,
            status=OrganizationStatus.ARCHIVED,
            archived_at=current,
            updated_at=current,
        )
        for team in await self._repository.list_teams(organization_id):
            if team.status is TeamStatus.ACTIVE:
                await self._repository.save_team(
                    replace(
                        team, status=TeamStatus.ARCHIVED, archived_at=current, updated_at=current
                    )
                )
        for membership in await self._repository.list_memberships(organization_id=organization_id):
            if membership.status in {MembershipStatus.ACTIVE, MembershipStatus.SUSPENDED}:
                await self._repository.save_membership(
                    replace(
                        membership,
                        status=MembershipStatus.REVOKED,
                        revoked_at=current,
                        suspended_at=membership.suspended_at,
                    )
                )
        return await self._repository.save_organization(archived)

    async def create_team(
        self,
        *,
        organization_id: str,
        name: str,
        description: str = "",
        parent_team_id: str | None = None,
        now: datetime | None = None,
    ) -> Team:
        organization = await self._repository.get_organization(organization_id)
        if organization.status is not OrganizationStatus.ACTIVE:
            raise ValueError("cannot create a team in an archived organization")
        if parent_team_id is not None:
            parent = await self._repository.get_team(parent_team_id)
            if parent.organization_id != organization_id:
                raise ValueError("parent team must belong to the same organization")
            if parent.status is not TeamStatus.ACTIVE:
                raise ValueError("parent team must be active")
        current = require_aware(now or utc_now(), "now")
        team = Team(
            organization_id=organization_id,
            name=name,
            description=description,
            parent_team_id=parent_team_id,
            created_at=current,
            updated_at=current,
        )
        return await self._repository.save_team(team)

    async def update_team(
        self,
        team_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        now: datetime | None = None,
    ) -> Team:
        team = await self._repository.get_team(team_id)
        if team.status is not TeamStatus.ACTIVE:
            raise ValueError("archived teams cannot be updated")
        current = require_aware(now or utc_now(), "now")
        updated = replace(
            team,
            name=team.name if name is None else name,
            description=team.description if description is None else description,
            updated_at=current,
        )
        return await self._repository.save_team(updated)

    async def add_member(
        self,
        *,
        actor_id: str,
        actor_type: ActorType,
        organization_id: str,
        team_id: str | None = None,
        role_refs: tuple[str, ...] = (),
        policy_refs: tuple[str, ...] = (),
        created_by_actor_id: str | None = None,
        now: datetime | None = None,
    ) -> Membership:
        await self._require_active_target(organization_id, team_id)
        existing = await self._find_live_membership(actor_id, organization_id, team_id)
        if existing is not None:
            raise ValueError("actor already has a live membership for this scope")
        current = require_aware(now or utc_now(), "now")
        membership = Membership(
            actor_id=actor_id,
            actor_type=actor_type,
            organization_id=organization_id,
            team_id=team_id,
            role_refs=role_refs,
            policy_refs=policy_refs,
            created_by_actor_id=created_by_actor_id,
            created_at=current,
            accepted_at=current,
        )
        return await self._repository.save_membership(membership)

    async def invite_member(
        self,
        *,
        organization_id: str,
        invited_by_actor_id: str,
        expires_at: datetime,
        token_ref: str,
        team_id: str | None = None,
        intended_identity_ref: str | None = None,
        intended_email_ref: str | None = None,
        role_refs: tuple[str, ...] = (),
        policy_refs: tuple[str, ...] = (),
        now: datetime | None = None,
    ) -> Invitation:
        await self._require_active_target(organization_id, team_id)
        current = require_aware(now or utc_now(), "now")
        invitation = Invitation(
            organization_id=organization_id,
            team_id=team_id,
            intended_identity_ref=intended_identity_ref,
            intended_email_ref=intended_email_ref,
            invited_by_actor_id=invited_by_actor_id,
            requested_role_refs=role_refs,
            requested_policy_refs=policy_refs,
            token_ref=token_ref,
            created_at=current,
            expires_at=expires_at,
        )
        return await self._repository.save_invitation(invitation)

    async def accept_invitation(
        self,
        invitation_id: str,
        *,
        actor_id: str,
        actor_type: ActorType = ActorType.HUMAN,
        now: datetime | None = None,
    ) -> Membership:
        invitation = await self._repository.get_invitation(invitation_id)
        current = require_aware(now or utc_now(), "now")
        if invitation.status is not InvitationStatus.PENDING:
            raise ValueError("invitation is not pending")
        if current >= invitation.expires_at:
            await self._repository.save_invitation(
                replace(invitation, status=InvitationStatus.EXPIRED)
            )
            raise ValueError("invitation has expired")
        if (
            invitation.intended_identity_ref is not None
            and invitation.intended_identity_ref != actor_id
        ):
            raise ValueError("invitation is bound to another identity")
        membership = await self.add_member(
            actor_id=actor_id,
            actor_type=actor_type,
            organization_id=invitation.organization_id,
            team_id=invitation.team_id,
            role_refs=invitation.requested_role_refs,
            policy_refs=invitation.requested_policy_refs,
            created_by_actor_id=invitation.invited_by_actor_id,
            now=current,
        )
        await self._repository.save_invitation(
            replace(invitation, status=InvitationStatus.ACCEPTED, accepted_at=current)
        )
        return membership

    async def expire_invitation(
        self,
        invitation_id: str,
        *,
        now: datetime | None = None,
    ) -> Invitation:
        invitation = await self._repository.get_invitation(invitation_id)
        current = require_aware(now or utc_now(), "now")
        if invitation.status is not InvitationStatus.PENDING:
            return invitation
        if current < invitation.expires_at:
            raise ValueError("invitation has not expired")
        return await self._repository.save_invitation(
            replace(invitation, status=InvitationStatus.EXPIRED)
        )

    async def revoke_invitation(
        self,
        invitation_id: str,
        *,
        now: datetime | None = None,
    ) -> Invitation:
        invitation = await self._repository.get_invitation(invitation_id)
        if invitation.status is not InvitationStatus.PENDING:
            raise ValueError("only pending invitations can be revoked")
        current = require_aware(now or utc_now(), "now")
        return await self._repository.save_invitation(
            replace(invitation, status=InvitationStatus.REVOKED, revoked_at=current)
        )

    async def set_membership_assignments(
        self,
        membership_id: str,
        *,
        role_refs: tuple[str, ...],
        policy_refs: tuple[str, ...],
    ) -> Membership:
        membership = await self._repository.get_membership(membership_id)
        if membership.status in {MembershipStatus.REVOKED, MembershipStatus.LEFT}:
            raise ValueError("revoked memberships cannot be reassigned")
        updated = replace(membership, role_refs=role_refs, policy_refs=policy_refs)
        return await self._repository.save_membership(updated)

    async def suspend_member(
        self,
        membership_id: str,
        *,
        now: datetime | None = None,
    ) -> Membership:
        membership = await self._repository.get_membership(membership_id)
        if membership.status is not MembershipStatus.ACTIVE:
            raise ValueError("only active memberships can be suspended")
        current = require_aware(now or utc_now(), "now")
        return await self._repository.save_membership(
            replace(membership, status=MembershipStatus.SUSPENDED, suspended_at=current)
        )

    async def remove_member(
        self,
        membership_id: str,
        *,
        now: datetime | None = None,
    ) -> Membership:
        membership = await self._repository.get_membership(membership_id)
        if membership.status in {MembershipStatus.REVOKED, MembershipStatus.LEFT}:
            return membership
        current = require_aware(now or utc_now(), "now")
        return await self._repository.save_membership(
            replace(membership, status=MembershipStatus.REVOKED, revoked_at=current)
        )

    async def leave_scope(
        self,
        membership_id: str,
        *,
        actor_id: str,
        now: datetime | None = None,
    ) -> Membership:
        membership = await self._repository.get_membership(membership_id)
        if membership.actor_id != actor_id:
            raise ValueError("actor can only leave its own membership")
        if membership.status is not MembershipStatus.ACTIVE:
            raise ValueError("only active memberships can be left")
        organization = await self._repository.get_organization(membership.organization_id)
        if organization.owner_actor_id == actor_id:
            raise ValueError("organization owner must transfer ownership before leaving")
        current = require_aware(now or utc_now(), "now")
        return await self._repository.save_membership(
            replace(membership, status=MembershipStatus.LEFT, revoked_at=current)
        )

    async def membership_authorization_scope(
        self,
        *,
        actor_id: str,
        organization_id: str | None = None,
    ) -> MembershipAuthorizationScope:
        if organization_id is None:
            return MembershipAuthorizationScope(
                actor_id=actor_id,
                organization_id=None,
                team_ids=(),
                role_refs=(),
                policy_refs=(),
            )
        memberships = await self._repository.list_memberships(
            actor_id=actor_id,
            organization_id=organization_id,
        )
        active = [item for item in memberships if item.status is MembershipStatus.ACTIVE]
        return MembershipAuthorizationScope(
            actor_id=actor_id,
            organization_id=organization_id,
            team_ids=tuple(sorted({item.team_id for item in active if item.team_id is not None})),
            role_refs=tuple(sorted({value for item in active for value in item.role_refs})),
            policy_refs=tuple(sorted({value for item in active for value in item.policy_refs})),
        )

    async def actor_identity_for_scope(
        self,
        *,
        actor_id: str,
        actor_type: ActorType,
        organization_id: str | None = None,
    ) -> ActorIdentity:
        scope = await self.membership_authorization_scope(
            actor_id=actor_id,
            organization_id=organization_id,
        )
        if organization_id is not None:
            memberships = await self._repository.list_memberships(
                actor_id=actor_id,
                organization_id=organization_id,
            )
            if not any(item.status is MembershipStatus.ACTIVE for item in memberships):
                raise PermissionError("actor has no active membership in organization")
        return ActorIdentity(
            actor_id=actor_id,
            actor_type=actor_type,
            organization_id=scope.organization_id,
            team_ids=scope.team_ids,
        )

    async def set_resource_owner(
        self,
        *,
        resource_type: str,
        resource_id: str,
        owner_ref: OwnerRef,
        organization_id: str | None = None,
        created_by_actor_id: str | None = None,
        now: datetime | None = None,
    ) -> ResourceOwnership:
        await self._validate_owner_scope(owner_ref, organization_id)
        current = require_aware(now or utc_now(), "now")
        ownership = ResourceOwnership(
            resource_type=resource_type,
            resource_id=resource_id,
            owner_ref=owner_ref,
            organization_id=organization_id,
            created_by_actor_id=created_by_actor_id,
            created_at=current,
            updated_at=current,
        )
        return await self._repository.save_ownership(ownership)

    async def transfer_resource(
        self,
        *,
        resource_type: str,
        resource_id: str,
        new_owner_ref: OwnerRef,
        organization_id: str | None,
        now: datetime | None = None,
    ) -> ResourceOwnership:
        current = require_aware(now or utc_now(), "now")
        existing = await self._repository.get_ownership(resource_type, resource_id)
        await self._validate_owner_scope(new_owner_ref, organization_id)
        updated = replace(
            existing,
            owner_ref=new_owner_ref,
            organization_id=organization_id,
            updated_at=current,
        )
        return await self._repository.save_ownership(updated)

    async def share_resource(
        self,
        *,
        resource_type: str,
        resource_id: str,
        target_ref: OwnerRef,
        granted_by_actor_id: str,
        policy_refs: tuple[str, ...] = (),
        allow_cross_organization: bool = False,
        now: datetime | None = None,
    ) -> ResourceShare:
        ownership = await self._repository.get_ownership(resource_type, resource_id)
        target_org = await self._organization_for_owner(target_ref)
        if (
            ownership.organization_id is not None
            and target_org is not None
            and ownership.organization_id != target_org
            and not allow_cross_organization
        ):
            raise PermissionError("cross-organization sharing is denied by default")
        current = require_aware(now or utc_now(), "now")
        share = ResourceShare(
            ownership_id=ownership.id,
            target_ref=target_ref,
            granted_by_actor_id=granted_by_actor_id,
            organization_id=target_org,
            policy_refs=policy_refs,
            created_at=current,
        )
        return await self._repository.save_share(share)

    async def revoke_share(
        self,
        share_id: str,
        *,
        now: datetime | None = None,
    ) -> ResourceShare:
        share = await self._repository.get_share(share_id)
        if share.status is ShareStatus.REVOKED:
            return share
        current = require_aware(now or utc_now(), "now")
        return await self._repository.save_share(
            replace(share, status=ShareStatus.REVOKED, revoked_at=current)
        )

    async def resource_in_actor_scope(
        self,
        *,
        actor_id: str,
        resource_type: str,
        resource_id: str,
    ) -> bool:
        """Return structural visibility only; #15 remains authoritative for actions."""

        ownership = await self._repository.get_ownership(resource_type, resource_id)
        if ownership.owner_ref.type in {"user", "service"} and ownership.owner_ref.id == actor_id:
            return True
        if ownership.organization_id is not None:
            memberships = await self._repository.list_memberships(
                actor_id=actor_id,
                organization_id=ownership.organization_id,
            )
            if ownership.owner_ref.type == "organization" and any(
                item.status is MembershipStatus.ACTIVE for item in memberships
            ):
                return True
            if ownership.owner_ref.type == "team" and any(
                item.status is MembershipStatus.ACTIVE and item.team_id == ownership.owner_ref.id
                for item in memberships
            ):
                return True
        for share in await self._repository.list_shares(ownership.id):
            if share.status is not ShareStatus.ACTIVE:
                continue
            if share.target_ref.type in {"user", "service"} and share.target_ref.id == actor_id:
                return True
            if share.organization_id is None:
                continue
            memberships = await self._repository.list_memberships(
                actor_id=actor_id,
                organization_id=share.organization_id,
            )
            if share.target_ref.type == "organization" and any(
                item.status is MembershipStatus.ACTIVE for item in memberships
            ):
                return True
            if share.target_ref.type == "team" and any(
                item.status is MembershipStatus.ACTIVE and item.team_id == share.target_ref.id
                for item in memberships
            ):
                return True
        return False

    async def add_external_group_mapping(
        self,
        *,
        provider_ref: str,
        external_group_id: str,
        organization_id: str,
        team_id: str | None = None,
        provisioning_mode: str = "manual",
        now: datetime | None = None,
    ) -> ExternalGroupMapping:
        await self._require_active_target(organization_id, team_id)
        current = require_aware(now or utc_now(), "now")
        mapping = ExternalGroupMapping(
            provider_ref=provider_ref,
            external_group_id=external_group_id,
            organization_id=organization_id,
            team_id=team_id,
            provisioning_mode=provisioning_mode,
            created_at=current,
        )
        return await self._repository.save_external_group_mapping(mapping)

    async def _require_active_target(
        self,
        organization_id: str,
        team_id: str | None,
    ) -> None:
        organization = await self._repository.get_organization(organization_id)
        if organization.status is not OrganizationStatus.ACTIVE:
            raise ValueError("organization is not active")
        if team_id is not None:
            team = await self._repository.get_team(team_id)
            if team.organization_id != organization_id:
                raise ValueError("team does not belong to organization")
            if team.status is not TeamStatus.ACTIVE:
                raise ValueError("team is not active")

    async def _find_live_membership(
        self,
        actor_id: str,
        organization_id: str,
        team_id: str | None,
    ) -> Membership | None:
        memberships = await self._repository.list_memberships(
            actor_id=actor_id,
            organization_id=organization_id,
        )
        for membership in memberships:
            if membership.team_id == team_id and membership.status in {
                MembershipStatus.ACTIVE,
                MembershipStatus.SUSPENDED,
            }:
                return membership
        return None

    async def _validate_owner_scope(
        self,
        owner_ref: OwnerRef,
        organization_id: str | None,
    ) -> None:
        if owner_ref.type == "organization":
            if organization_id != owner_ref.id:
                raise ValueError("organization owner requires matching organization scope")
            await self._repository.get_organization(owner_ref.id)
        elif owner_ref.type == "team":
            team = await self._repository.get_team(owner_ref.id)
            if organization_id != team.organization_id:
                raise ValueError("team owner requires its organization scope")
        elif organization_id is not None:
            await self._repository.get_organization(organization_id)

    async def _organization_for_owner(self, owner_ref: OwnerRef) -> str | None:
        if owner_ref.type == "organization":
            await self._repository.get_organization(owner_ref.id)
            return owner_ref.id
        if owner_ref.type == "team":
            team = await self._repository.get_team(owner_ref.id)
            return team.organization_id
        return None
