"""Repository port and deterministic in-memory reference store for organization resources."""

from __future__ import annotations

from typing import Protocol

from .models import (
    ExternalGroupMapping,
    Invitation,
    Membership,
    Organization,
    ResourceOwnership,
    ResourceShare,
    Team,
)


class OrganizationRepository(Protocol):
    async def save_organization(self, organization: Organization) -> Organization: ...
    async def get_organization(self, organization_id: str) -> Organization: ...
    async def list_organizations(self) -> tuple[Organization, ...]: ...

    async def save_team(self, team: Team) -> Team: ...
    async def get_team(self, team_id: str) -> Team: ...
    async def list_teams(self, organization_id: str | None = None) -> tuple[Team, ...]: ...

    async def save_membership(self, membership: Membership) -> Membership: ...
    async def get_membership(self, membership_id: str) -> Membership: ...
    async def list_memberships(
        self,
        *,
        actor_id: str | None = None,
        organization_id: str | None = None,
        team_id: str | None = None,
    ) -> tuple[Membership, ...]: ...

    async def save_invitation(self, invitation: Invitation) -> Invitation: ...
    async def get_invitation(self, invitation_id: str) -> Invitation: ...
    async def list_invitations(
        self,
        organization_id: str | None = None,
    ) -> tuple[Invitation, ...]: ...

    async def save_ownership(self, ownership: ResourceOwnership) -> ResourceOwnership: ...
    async def get_ownership(self, resource_type: str, resource_id: str) -> ResourceOwnership: ...
    async def list_ownerships(self) -> tuple[ResourceOwnership, ...]: ...

    async def save_share(self, share: ResourceShare) -> ResourceShare: ...
    async def get_share(self, share_id: str) -> ResourceShare: ...
    async def list_shares(self, ownership_id: str) -> tuple[ResourceShare, ...]: ...
    async def list_all_shares(self) -> tuple[ResourceShare, ...]: ...

    async def save_external_group_mapping(
        self,
        mapping: ExternalGroupMapping,
    ) -> ExternalGroupMapping: ...
    async def list_external_group_mappings(
        self,
        organization_id: str | None = None,
    ) -> tuple[ExternalGroupMapping, ...]: ...


class InMemoryOrganizationRepository:
    """Reference repository. Production persistence can implement the same port."""

    def __init__(self) -> None:
        self._organizations: dict[str, Organization] = {}
        self._teams: dict[str, Team] = {}
        self._memberships: dict[str, Membership] = {}
        self._invitations: dict[str, Invitation] = {}
        self._ownership: dict[tuple[str, str], ResourceOwnership] = {}
        self._shares: dict[str, ResourceShare] = {}
        self._external_groups: dict[str, ExternalGroupMapping] = {}

    async def save_organization(self, organization: Organization) -> Organization:
        self._organizations[organization.id] = organization
        return organization

    async def get_organization(self, organization_id: str) -> Organization:
        try:
            return self._organizations[organization_id]
        except KeyError as exc:
            raise LookupError(f"organization not found: {organization_id}") from exc

    async def list_organizations(self) -> tuple[Organization, ...]:
        return tuple(sorted(self._organizations.values(), key=lambda item: item.id))

    async def save_team(self, team: Team) -> Team:
        self._teams[team.id] = team
        return team

    async def get_team(self, team_id: str) -> Team:
        try:
            return self._teams[team_id]
        except KeyError as exc:
            raise LookupError(f"team not found: {team_id}") from exc

    async def list_teams(self, organization_id: str | None = None) -> tuple[Team, ...]:
        values = tuple(
            item
            for item in self._teams.values()
            if organization_id is None or item.organization_id == organization_id
        )
        return tuple(sorted(values, key=lambda item: item.id))

    async def save_membership(self, membership: Membership) -> Membership:
        self._memberships[membership.id] = membership
        return membership

    async def get_membership(self, membership_id: str) -> Membership:
        try:
            return self._memberships[membership_id]
        except KeyError as exc:
            raise LookupError(f"membership not found: {membership_id}") from exc

    async def list_memberships(
        self,
        *,
        actor_id: str | None = None,
        organization_id: str | None = None,
        team_id: str | None = None,
    ) -> tuple[Membership, ...]:
        values = tuple(
            item
            for item in self._memberships.values()
            if (actor_id is None or item.actor_id == actor_id)
            and (organization_id is None or item.organization_id == organization_id)
            and (team_id is None or item.team_id == team_id)
        )
        return tuple(sorted(values, key=lambda item: item.id))

    async def save_invitation(self, invitation: Invitation) -> Invitation:
        self._invitations[invitation.id] = invitation
        return invitation

    async def get_invitation(self, invitation_id: str) -> Invitation:
        try:
            return self._invitations[invitation_id]
        except KeyError as exc:
            raise LookupError(f"invitation not found: {invitation_id}") from exc

    async def list_invitations(
        self,
        organization_id: str | None = None,
    ) -> tuple[Invitation, ...]:
        values = tuple(
            item
            for item in self._invitations.values()
            if organization_id is None or item.organization_id == organization_id
        )
        return tuple(sorted(values, key=lambda item: item.id))

    async def save_ownership(self, ownership: ResourceOwnership) -> ResourceOwnership:
        self._ownership[(ownership.resource_type, ownership.resource_id)] = ownership
        return ownership

    async def get_ownership(self, resource_type: str, resource_id: str) -> ResourceOwnership:
        try:
            return self._ownership[(resource_type, resource_id)]
        except KeyError as exc:
            raise LookupError(
                f"resource ownership not found: {resource_type}/{resource_id}"
            ) from exc

    async def list_ownerships(self) -> tuple[ResourceOwnership, ...]:
        return tuple(sorted(self._ownership.values(), key=lambda item: item.id))

    async def save_share(self, share: ResourceShare) -> ResourceShare:
        self._shares[share.id] = share
        return share

    async def get_share(self, share_id: str) -> ResourceShare:
        try:
            return self._shares[share_id]
        except KeyError as exc:
            raise LookupError(f"resource share not found: {share_id}") from exc

    async def list_shares(self, ownership_id: str) -> tuple[ResourceShare, ...]:
        return tuple(
            sorted(
                (item for item in self._shares.values() if item.ownership_id == ownership_id),
                key=lambda item: item.id,
            )
        )

    async def list_all_shares(self) -> tuple[ResourceShare, ...]:
        return tuple(sorted(self._shares.values(), key=lambda item: item.id))

    async def save_external_group_mapping(
        self,
        mapping: ExternalGroupMapping,
    ) -> ExternalGroupMapping:
        self._external_groups[mapping.id] = mapping
        return mapping

    async def list_external_group_mappings(
        self,
        organization_id: str | None = None,
    ) -> tuple[ExternalGroupMapping, ...]:
        values = tuple(
            item
            for item in self._external_groups.values()
            if organization_id is None or item.organization_id == organization_id
        )
        return tuple(sorted(values, key=lambda item: item.id))
