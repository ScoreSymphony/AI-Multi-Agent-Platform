"""Event-loop-safe SQLite adapter for organization persistence."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import ContractError

from . import sqlite as _legacy
from ._sqlite_async import AsyncSqliteOffload, map_contract_sqlite_error
from .models import (
    ExternalGroupMapping,
    Invitation,
    Membership,
    Organization,
    ResourceOwnership,
    ResourceShare,
    Team,
)
from .sqlite import SqliteOrganizationRepository as _SyncSqliteOrganizationRepository


class SqliteOrganizationRepository(_SyncSqliteOrganizationRepository):
    """Organization SQLite repository whose awaitable I/O never blocks the event loop."""

    def __init__(self, db_path: str | Path, *, max_concurrency: int = 4) -> None:
        super().__init__(db_path)
        self._async_sqlite = AsyncSqliteOffload(max_concurrency=max_concurrency)

    async def _run_sqlite[T](
        self,
        operation: Callable[[], T],
        *,
        message: str,
        write: bool = False,
    ) -> T:
        try:
            return await self._async_sqlite.run(operation, write=write)
        except ContractError as exc:
            mapped = map_contract_sqlite_error(exc, message)
            if mapped is exc:
                raise
            raise mapped from exc.__cause__

    async def save_organization(self, organization: Organization) -> Organization:
        def operation() -> Organization:
            self._save(
                "organization",
                organization.id,
                _legacy._organization_json(organization),
                organization_id=organization.id,
            )
            return organization

        return await self._run_sqlite(
            operation,
            message="failed to persist organization",
            write=True,
        )

    async def get_organization(self, organization_id: str) -> Organization:
        return cast(
            Organization,
            await self._run_sqlite(
                lambda: self._load("organization", organization_id),
                message="failed to read organization",
            ),
        )

    async def list_organizations(self) -> tuple[Organization, ...]:
        return cast(
            tuple[Organization, ...],
            await self._run_sqlite(
                lambda: self._list("organization"),
                message="failed to list organizations",
            ),
        )

    async def save_team(self, team: Team) -> Team:
        def operation() -> Team:
            self._save(
                "team",
                team.id,
                _legacy._team_json(team),
                organization_id=team.organization_id,
                team_id=team.id,
            )
            return team

        return await self._run_sqlite(operation, message="failed to persist team", write=True)

    async def get_team(self, team_id: str) -> Team:
        return cast(
            Team,
            await self._run_sqlite(
                lambda: self._load("team", team_id),
                message="failed to read team",
            ),
        )

    async def list_teams(self, organization_id: str | None = None) -> tuple[Team, ...]:
        return cast(
            tuple[Team, ...],
            await self._run_sqlite(
                lambda: self._list("team", organization_id=organization_id),
                message="failed to list teams",
            ),
        )

    async def save_membership(self, membership: Membership) -> Membership:
        def operation() -> Membership:
            self._save(
                "membership",
                membership.id,
                _legacy._membership_json(membership),
                organization_id=membership.organization_id,
                team_id=membership.team_id,
                actor_id=membership.actor_id,
            )
            return membership

        return await self._run_sqlite(
            operation,
            message="failed to persist membership",
            write=True,
        )

    async def get_membership(self, membership_id: str) -> Membership:
        return cast(
            Membership,
            await self._run_sqlite(
                lambda: self._load("membership", membership_id),
                message="failed to read membership",
            ),
        )

    async def list_memberships(
        self,
        *,
        actor_id: str | None = None,
        organization_id: str | None = None,
        team_id: str | None = None,
    ) -> tuple[Membership, ...]:
        return cast(
            tuple[Membership, ...],
            await self._run_sqlite(
                lambda: self._list(
                    "membership",
                    actor_id=actor_id,
                    organization_id=organization_id,
                    team_id=team_id,
                ),
                message="failed to list memberships",
            ),
        )

    async def save_invitation(self, invitation: Invitation) -> Invitation:
        def operation() -> Invitation:
            self._save(
                "invitation",
                invitation.id,
                _legacy._invitation_json(invitation),
                organization_id=invitation.organization_id,
                team_id=invitation.team_id,
            )
            return invitation

        return await self._run_sqlite(
            operation,
            message="failed to persist invitation",
            write=True,
        )

    async def get_invitation(self, invitation_id: str) -> Invitation:
        return cast(
            Invitation,
            await self._run_sqlite(
                lambda: self._load("invitation", invitation_id),
                message="failed to read invitation",
            ),
        )

    async def list_invitations(
        self,
        organization_id: str | None = None,
    ) -> tuple[Invitation, ...]:
        return cast(
            tuple[Invitation, ...],
            await self._run_sqlite(
                lambda: self._list("invitation", organization_id=organization_id),
                message="failed to list invitations",
            ),
        )

    async def save_ownership(self, ownership: ResourceOwnership) -> ResourceOwnership:
        def operation() -> ResourceOwnership:
            self._save(
                "ownership",
                ownership.id,
                _legacy._ownership_json(ownership),
                organization_id=ownership.organization_id,
                resource_type=ownership.resource_type,
                resource_id=ownership.resource_id,
            )
            return ownership

        return await self._run_sqlite(
            operation,
            message="failed to persist resource ownership",
            write=True,
        )

    async def get_ownership(self, resource_type: str, resource_id: str) -> ResourceOwnership:
        def operation() -> ResourceOwnership:
            values = self._list(
                "ownership",
                resource_type=resource_type,
                resource_id=resource_id,
            )
            if not values:
                raise LookupError(f"resource ownership not found: {resource_type}/{resource_id}")
            return cast(ResourceOwnership, values[0])

        return await self._run_sqlite(operation, message="failed to read resource ownership")

    async def list_ownerships(self) -> tuple[ResourceOwnership, ...]:
        return cast(
            tuple[ResourceOwnership, ...],
            await self._run_sqlite(
                lambda: self._list("ownership"),
                message="failed to list resource ownerships",
            ),
        )

    async def save_share(self, share: ResourceShare) -> ResourceShare:
        def operation() -> ResourceShare:
            self._save(
                "share",
                share.id,
                _legacy._share_json(share),
                organization_id=share.organization_id,
                ownership_id=share.ownership_id,
            )
            return share

        return await self._run_sqlite(
            operation,
            message="failed to persist resource share",
            write=True,
        )

    async def get_share(self, share_id: str) -> ResourceShare:
        return cast(
            ResourceShare,
            await self._run_sqlite(
                lambda: self._load("share", share_id),
                message="failed to read resource share",
            ),
        )

    async def list_shares(self, ownership_id: str) -> tuple[ResourceShare, ...]:
        return cast(
            tuple[ResourceShare, ...],
            await self._run_sqlite(
                lambda: self._list("share", ownership_id=ownership_id),
                message="failed to list resource shares",
            ),
        )

    async def list_all_shares(self) -> tuple[ResourceShare, ...]:
        return cast(
            tuple[ResourceShare, ...],
            await self._run_sqlite(
                lambda: self._list("share"),
                message="failed to list all resource shares",
            ),
        )

    async def save_external_group_mapping(
        self,
        mapping: ExternalGroupMapping,
    ) -> ExternalGroupMapping:
        def operation() -> ExternalGroupMapping:
            self._save(
                "external_group_mapping",
                mapping.id,
                _legacy._external_group_json(mapping),
                organization_id=mapping.organization_id,
                team_id=mapping.team_id,
            )
            return mapping

        return await self._run_sqlite(
            operation,
            message="failed to persist external group mapping",
            write=True,
        )

    async def list_external_group_mappings(
        self,
        organization_id: str | None = None,
    ) -> tuple[ExternalGroupMapping, ...]:
        return cast(
            tuple[ExternalGroupMapping, ...],
            await self._run_sqlite(
                lambda: self._list(
                    "external_group_mapping",
                    organization_id=organization_id,
                ),
                message="failed to list external group mappings",
            ),
        )


__all__ = ["SqliteOrganizationRepository"]
