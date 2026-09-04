"""Restart-safe SQLite reference persistence for organization resources."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security.authorization import ActorType

from .models import (
    ExternalGroupMapping,
    Invitation,
    InvitationStatus,
    Membership,
    MembershipStatus,
    Organization,
    OrganizationStatus,
    ResourceOwnership,
    ResourceShare,
    ShareStatus,
    Team,
    TeamStatus,
)
from .repository import OrganizationRepository


class SqliteOrganizationRepository(OrganizationRepository):
    """Dependency-free durable implementation of the organization repository port."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS organization_records (
                        kind TEXT NOT NULL,
                        id TEXT NOT NULL,
                        organization_id TEXT,
                        team_id TEXT,
                        actor_id TEXT,
                        resource_type TEXT,
                        resource_id TEXT,
                        ownership_id TEXT,
                        payload TEXT NOT NULL,
                        PRIMARY KEY(kind, id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_organization_records_scope
                    ON organization_records(kind, organization_id, team_id)
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_organization_records_actor
                    ON organization_records(kind, actor_id, organization_id)
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_organization_records_ownership
                    ON organization_records(kind, ownership_id)
                    """
                )
                connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_organization_ownership_resource
                    ON organization_records(resource_type, resource_id)
                    WHERE kind = 'ownership'
                    """
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to initialize organization storage",
            ) from exc

    async def save_organization(self, organization: Organization) -> Organization:
        self._save(
            "organization",
            organization.id,
            _organization_json(organization),
            organization_id=organization.id,
        )
        return organization

    async def get_organization(self, organization_id: str) -> Organization:
        return cast(Organization, self._load("organization", organization_id))

    async def list_organizations(self) -> tuple[Organization, ...]:
        return cast(tuple[Organization, ...], self._list("organization"))

    async def save_team(self, team: Team) -> Team:
        self._save(
            "team",
            team.id,
            _team_json(team),
            organization_id=team.organization_id,
            team_id=team.id,
        )
        return team

    async def get_team(self, team_id: str) -> Team:
        return cast(Team, self._load("team", team_id))

    async def list_teams(self, organization_id: str | None = None) -> tuple[Team, ...]:
        return cast(
            tuple[Team, ...],
            self._list("team", organization_id=organization_id),
        )

    async def save_membership(self, membership: Membership) -> Membership:
        self._save(
            "membership",
            membership.id,
            _membership_json(membership),
            organization_id=membership.organization_id,
            team_id=membership.team_id,
            actor_id=membership.actor_id,
        )
        return membership

    async def get_membership(self, membership_id: str) -> Membership:
        return cast(Membership, self._load("membership", membership_id))

    async def list_memberships(
        self,
        *,
        actor_id: str | None = None,
        organization_id: str | None = None,
        team_id: str | None = None,
    ) -> tuple[Membership, ...]:
        return cast(
            tuple[Membership, ...],
            self._list(
                "membership",
                actor_id=actor_id,
                organization_id=organization_id,
                team_id=team_id,
            ),
        )

    async def save_invitation(self, invitation: Invitation) -> Invitation:
        self._save(
            "invitation",
            invitation.id,
            _invitation_json(invitation),
            organization_id=invitation.organization_id,
            team_id=invitation.team_id,
        )
        return invitation

    async def get_invitation(self, invitation_id: str) -> Invitation:
        return cast(Invitation, self._load("invitation", invitation_id))

    async def list_invitations(
        self,
        organization_id: str | None = None,
    ) -> tuple[Invitation, ...]:
        return cast(
            tuple[Invitation, ...],
            self._list("invitation", organization_id=organization_id),
        )

    async def save_ownership(self, ownership: ResourceOwnership) -> ResourceOwnership:
        self._save(
            "ownership",
            ownership.id,
            _ownership_json(ownership),
            organization_id=ownership.organization_id,
            resource_type=ownership.resource_type,
            resource_id=ownership.resource_id,
        )
        return ownership

    async def get_ownership(self, resource_type: str, resource_id: str) -> ResourceOwnership:
        values = self._list(
            "ownership",
            resource_type=resource_type,
            resource_id=resource_id,
        )
        if not values:
            raise LookupError(f"resource ownership not found: {resource_type}/{resource_id}")
        return cast(ResourceOwnership, values[0])

    async def list_ownerships(self) -> tuple[ResourceOwnership, ...]:
        return cast(tuple[ResourceOwnership, ...], self._list("ownership"))

    async def save_share(self, share: ResourceShare) -> ResourceShare:
        self._save(
            "share",
            share.id,
            _share_json(share),
            organization_id=share.organization_id,
            ownership_id=share.ownership_id,
        )
        return share

    async def get_share(self, share_id: str) -> ResourceShare:
        return cast(ResourceShare, self._load("share", share_id))

    async def list_shares(self, ownership_id: str) -> tuple[ResourceShare, ...]:
        return cast(
            tuple[ResourceShare, ...],
            self._list("share", ownership_id=ownership_id),
        )

    async def list_all_shares(self) -> tuple[ResourceShare, ...]:
        return cast(tuple[ResourceShare, ...], self._list("share"))

    async def save_external_group_mapping(
        self,
        mapping: ExternalGroupMapping,
    ) -> ExternalGroupMapping:
        self._save(
            "external_group_mapping",
            mapping.id,
            _external_group_json(mapping),
            organization_id=mapping.organization_id,
            team_id=mapping.team_id,
        )
        return mapping

    async def list_external_group_mappings(
        self,
        organization_id: str | None = None,
    ) -> tuple[ExternalGroupMapping, ...]:
        return cast(
            tuple[ExternalGroupMapping, ...],
            self._list("external_group_mapping", organization_id=organization_id),
        )

    def _save(
        self,
        kind: str,
        record_id: str,
        payload: dict[str, Any],
        *,
        organization_id: str | None = None,
        team_id: str | None = None,
        actor_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        ownership_id: str | None = None,
    ) -> None:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO organization_records(
                        kind, id, organization_id, team_id, actor_id,
                        resource_type, resource_id, ownership_id, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(kind, id) DO UPDATE SET
                        organization_id = excluded.organization_id,
                        team_id = excluded.team_id,
                        actor_id = excluded.actor_id,
                        resource_type = excluded.resource_type,
                        resource_id = excluded.resource_id,
                        ownership_id = excluded.ownership_id,
                        payload = excluded.payload
                    """,
                    (
                        kind,
                        record_id,
                        organization_id,
                        team_id,
                        actor_id,
                        resource_type,
                        resource_id,
                        ownership_id,
                        encoded,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"organization persistence conflict for {kind}: {record_id}",
            ) from exc
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"failed to persist organization record: {kind}",
            ) from exc

    def _load(self, kind: str, record_id: str) -> object:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload FROM organization_records WHERE kind = ? AND id = ?",
                    (kind, record_id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"failed to read organization record: {kind}",
            ) from exc
        if row is None:
            raise LookupError(f"{kind} not found: {record_id}")
        return _decode(kind, cast(str, row["payload"]))

    def _list(
        self,
        kind: str,
        *,
        organization_id: str | None = None,
        team_id: str | None = None,
        actor_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        ownership_id: str | None = None,
    ) -> tuple[object, ...]:
        clauses = ["kind = ?"]
        values: list[object] = [kind]
        for column, value in (
            ("organization_id", organization_id),
            ("team_id", team_id),
            ("actor_id", actor_id),
            ("resource_type", resource_type),
            ("resource_id", resource_id),
            ("ownership_id", ownership_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        query = (
            f"SELECT payload FROM organization_records WHERE {' AND '.join(clauses)} ORDER BY id"
        )
        try:
            with self._connect() as connection:
                rows = connection.execute(query, tuple(values)).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"failed to list organization records: {kind}",
            ) from exc
        return tuple(_decode(kind, cast(str, row["payload"])) for row in rows)


def _organization_json(value: Organization) -> dict[str, Any]:
    return {
        "id": value.id,
        "name": value.name,
        "owner_actor_id": value.owner_actor_id,
        "display_name": value.display_name,
        "status": value.status.value,
        "administrator_actor_ids": list(value.administrator_actor_ids),
        "settings": value.settings,
        "default_policy_refs": list(value.default_policy_refs),
        "default_configuration_refs": list(value.default_configuration_refs),
        "provenance": value.provenance,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
        "archived_at": _optional_time_json(value.archived_at),
    }


def _team_json(value: Team) -> dict[str, Any]:
    return {
        "id": value.id,
        "organization_id": value.organization_id,
        "name": value.name,
        "description": value.description,
        "status": value.status.value,
        "parent_team_id": value.parent_team_id,
        "project_scope_refs": list(value.project_scope_refs),
        "default_policy_refs": list(value.default_policy_refs),
        "default_configuration_refs": list(value.default_configuration_refs),
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
        "archived_at": _optional_time_json(value.archived_at),
    }


def _membership_json(value: Membership) -> dict[str, Any]:
    return {
        "id": value.id,
        "actor_id": value.actor_id,
        "actor_type": value.actor_type.value,
        "organization_id": value.organization_id,
        "team_id": value.team_id,
        "status": value.status.value,
        "role_refs": list(value.role_refs),
        "policy_refs": list(value.policy_refs),
        "created_by_actor_id": value.created_by_actor_id,
        "invited_by_actor_id": value.invited_by_actor_id,
        "created_at": value.created_at.isoformat(),
        "accepted_at": value.accepted_at.isoformat(),
        "suspended_at": _optional_time_json(value.suspended_at),
        "revoked_at": _optional_time_json(value.revoked_at),
        "expires_at": _optional_time_json(value.expires_at),
    }


def _invitation_json(value: Invitation) -> dict[str, Any]:
    return {
        "id": value.id,
        "organization_id": value.organization_id,
        "team_id": value.team_id,
        "intended_identity_ref": value.intended_identity_ref,
        "intended_email_ref": value.intended_email_ref,
        "invited_by_actor_id": value.invited_by_actor_id,
        "requested_role_refs": list(value.requested_role_refs),
        "requested_policy_refs": list(value.requested_policy_refs),
        "status": value.status.value,
        "token_ref": value.token_ref,
        "created_at": value.created_at.isoformat(),
        "expires_at": value.expires_at.isoformat(),
        "accepted_at": _optional_time_json(value.accepted_at),
        "revoked_at": _optional_time_json(value.revoked_at),
    }


def _ownership_json(value: ResourceOwnership) -> dict[str, Any]:
    return {
        "id": value.id,
        "resource_type": value.resource_type,
        "resource_id": value.resource_id,
        "owner_ref": {"type": value.owner_ref.type, "id": value.owner_ref.id},
        "organization_id": value.organization_id,
        "created_by_actor_id": value.created_by_actor_id,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
    }


def _share_json(value: ResourceShare) -> dict[str, Any]:
    return {
        "id": value.id,
        "ownership_id": value.ownership_id,
        "target_ref": {"type": value.target_ref.type, "id": value.target_ref.id},
        "granted_by_actor_id": value.granted_by_actor_id,
        "organization_id": value.organization_id,
        "status": value.status.value,
        "policy_refs": list(value.policy_refs),
        "created_at": value.created_at.isoformat(),
        "revoked_at": _optional_time_json(value.revoked_at),
    }


def _external_group_json(value: ExternalGroupMapping) -> dict[str, Any]:
    return {
        "id": value.id,
        "provider_ref": value.provider_ref,
        "external_group_id": value.external_group_id,
        "organization_id": value.organization_id,
        "team_id": value.team_id,
        "provisioning_mode": value.provisioning_mode,
        "active": value.active,
        "created_at": value.created_at.isoformat(),
    }


def _decode(kind: str, encoded: str) -> object:
    try:
        raw = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "stored organization JSON is invalid",
        ) from exc
    if not isinstance(raw, dict):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "stored organization JSON is not an object",
        )
    data = cast(dict[str, Any], raw)
    try:
        if kind == "organization":
            return Organization(
                id=cast(str, data["id"]),
                name=cast(str, data["name"]),
                owner_actor_id=cast(str, data["owner_actor_id"]),
                display_name=cast(str | None, data.get("display_name")),
                status=OrganizationStatus(cast(str, data["status"])),
                administrator_actor_ids=_strings(data.get("administrator_actor_ids", [])),
                settings=cast(dict[str, JsonValue], data.get("settings", {})),
                default_policy_refs=_strings(data.get("default_policy_refs", [])),
                default_configuration_refs=_strings(data.get("default_configuration_refs", [])),
                provenance=cast(dict[str, JsonValue], data.get("provenance", {})),
                created_at=_time(data["created_at"]),
                updated_at=_time(data["updated_at"]),
                archived_at=_optional_time(data.get("archived_at")),
            )
        if kind == "team":
            return Team(
                id=cast(str, data["id"]),
                organization_id=cast(str, data["organization_id"]),
                name=cast(str, data["name"]),
                description=cast(str, data.get("description", "")),
                status=TeamStatus(cast(str, data["status"])),
                parent_team_id=cast(str | None, data.get("parent_team_id")),
                project_scope_refs=_strings(data.get("project_scope_refs", [])),
                default_policy_refs=_strings(data.get("default_policy_refs", [])),
                default_configuration_refs=_strings(data.get("default_configuration_refs", [])),
                created_at=_time(data["created_at"]),
                updated_at=_time(data["updated_at"]),
                archived_at=_optional_time(data.get("archived_at")),
            )
        if kind == "membership":
            return Membership(
                id=cast(str, data["id"]),
                actor_id=cast(str, data["actor_id"]),
                actor_type=ActorType(cast(str, data["actor_type"])),
                organization_id=cast(str, data["organization_id"]),
                team_id=cast(str | None, data.get("team_id")),
                status=MembershipStatus(cast(str, data["status"])),
                role_refs=_strings(data.get("role_refs", [])),
                policy_refs=_strings(data.get("policy_refs", [])),
                created_by_actor_id=cast(str | None, data.get("created_by_actor_id")),
                invited_by_actor_id=cast(str | None, data.get("invited_by_actor_id")),
                created_at=_time(data["created_at"]),
                accepted_at=_time(data["accepted_at"]),
                suspended_at=_optional_time(data.get("suspended_at")),
                revoked_at=_optional_time(data.get("revoked_at")),
                expires_at=_optional_time(data.get("expires_at")),
            )
        if kind == "invitation":
            return Invitation(
                id=cast(str, data["id"]),
                organization_id=cast(str, data["organization_id"]),
                team_id=cast(str | None, data.get("team_id")),
                intended_identity_ref=cast(str | None, data.get("intended_identity_ref")),
                intended_email_ref=cast(str | None, data.get("intended_email_ref")),
                invited_by_actor_id=cast(str, data["invited_by_actor_id"]),
                requested_role_refs=_strings(data.get("requested_role_refs", [])),
                requested_policy_refs=_strings(data.get("requested_policy_refs", [])),
                status=InvitationStatus(cast(str, data["status"])),
                token_ref=cast(str, data["token_ref"]),
                created_at=_time(data["created_at"]),
                expires_at=_time(data["expires_at"]),
                accepted_at=_optional_time(data.get("accepted_at")),
                revoked_at=_optional_time(data.get("revoked_at")),
            )
        if kind == "ownership":
            owner = _owner_ref(data["owner_ref"])
            return ResourceOwnership(
                id=cast(str, data["id"]),
                resource_type=cast(str, data["resource_type"]),
                resource_id=cast(str, data["resource_id"]),
                owner_ref=owner,
                organization_id=cast(str | None, data.get("organization_id")),
                created_by_actor_id=cast(str | None, data.get("created_by_actor_id")),
                created_at=_time(data["created_at"]),
                updated_at=_time(data["updated_at"]),
            )
        if kind == "share":
            return ResourceShare(
                id=cast(str, data["id"]),
                ownership_id=cast(str, data["ownership_id"]),
                target_ref=_owner_ref(data["target_ref"]),
                granted_by_actor_id=cast(str, data["granted_by_actor_id"]),
                organization_id=cast(str | None, data.get("organization_id")),
                status=ShareStatus(cast(str, data["status"])),
                policy_refs=_strings(data.get("policy_refs", [])),
                created_at=_time(data["created_at"]),
                revoked_at=_optional_time(data.get("revoked_at")),
            )
        if kind == "external_group_mapping":
            return ExternalGroupMapping(
                id=cast(str, data["id"]),
                provider_ref=cast(str, data["provider_ref"]),
                external_group_id=cast(str, data["external_group_id"]),
                organization_id=cast(str, data["organization_id"]),
                team_id=cast(str | None, data.get("team_id")),
                provisioning_mode=cast(str, data["provisioning_mode"]),
                active=cast(bool, data["active"]),
                created_at=_time(data["created_at"]),
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"stored organization record is invalid: {kind}",
        ) from exc
    raise ContractError(
        ErrorCode.CONTRACT_VIOLATION,
        f"unsupported stored organization record kind: {kind}",
    )


def _owner_ref(value: object) -> OwnerRef:
    if not isinstance(value, dict):
        raise ValueError("stored owner reference must be an object")
    raw = cast(dict[str, Any], value)
    return OwnerRef(
        type=cast(Any, raw["type"]),
        id=cast(str, raw["id"]),
    )


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("stored string collection is invalid")
    return tuple(cast(list[str], value))


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("stored timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored timestamp must be timezone-aware")
    return parsed


def _optional_time(value: object) -> datetime | None:
    return None if value is None else _time(value)


def _optional_time_json(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()
