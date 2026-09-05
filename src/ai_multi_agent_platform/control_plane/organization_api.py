"""Canonical organization/team/membership Control Plane composition for issue #87."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime
from typing import Any, Literal, TypeVar, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.organizations import (
    ExternalGroupMapping,
    Invitation,
    Membership,
    Organization,
    OrganizationService,
    ResourceOwnership,
    ResourceShare,
    Team,
)
from ai_multi_agent_platform.security.authorization import ActorType

from .extensions import CommandHandler, ResourceService
from .models import PageQuery, RequestContext
from .plugin_terminal_composition import ControlPlane as _CurrentControlPlane

ORGANIZATION_COLLECTION = "organizations"
TEAM_COLLECTION = "teams"
MEMBERSHIP_COLLECTION = "memberships"
INVITATION_COLLECTION = "invitations"
RESOURCE_OWNERSHIP_COLLECTION = "resource-ownerships"
RESOURCE_SHARE_COLLECTION = "resource-shares"
EXTERNAL_GROUP_MAPPING_COLLECTION = "external-group-mappings"

ORGANIZATION_COLLECTIONS = (
    ORGANIZATION_COLLECTION,
    TEAM_COLLECTION,
    MEMBERSHIP_COLLECTION,
    INVITATION_COLLECTION,
    RESOURCE_OWNERSHIP_COLLECTION,
    RESOURCE_SHARE_COLLECTION,
    EXTERNAL_GROUP_MAPPING_COLLECTION,
)

ORGANIZATION_COMMANDS = (
    "organization.create",
    "organization.archive",
    "team.create",
    "team.update",
    "membership.add",
    "membership.assign",
    "membership.suspend",
    "membership.remove",
    "invitation.create",
    "invitation.accept",
    "invitation.revoke",
    "resource-ownership.set",
    "resource-ownership.transfer",
    "resource-share.create",
    "resource-share.revoke",
    "external-group-mapping.create",
)

CollectionName = Literal[
    "organizations",
    "teams",
    "memberships",
    "invitations",
    "resource-ownerships",
    "resource-shares",
    "external-group-mappings",
]
ScopeType = Literal["organization", "team"]
OwnerKind = Literal["user", "organization", "team", "service"]
T = TypeVar("T")


class _OrganizationResources(ResourceService):
    """Scope-aware northbound projection for one canonical organization collection."""

    def __init__(self, service: OrganizationService, collection: CollectionName) -> None:
        self._service = service
        self._collection = collection
        self.search_indexable = collection in {
            ORGANIZATION_COLLECTION,
            TEAM_COLLECTION,
            MEMBERSHIP_COLLECTION,
        }

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        principal = context.actor.principal_ref
        visible_orgs = await _visible_organization_ids(self._service, principal)

        if self._collection == ORGANIZATION_COLLECTION:
            organizations = await self._service.repository.list_organizations()
            return tuple(
                _organization_resource(organization)
                for organization in organizations
                if organization.id in visible_orgs
            )
        if self._collection == TEAM_COLLECTION:
            teams = await self._service.repository.list_teams()
            return tuple(
                _team_resource(team) for team in teams if team.organization_id in visible_orgs
            )
        if self._collection == MEMBERSHIP_COLLECTION:
            memberships = await self._service.repository.list_memberships()
            return tuple(
                _membership_resource(membership)
                for membership in memberships
                if membership.organization_id in visible_orgs
            )
        if self._collection == INVITATION_COLLECTION:
            invitations = await self._service.repository.list_invitations()
            visible_invitations: list[dict[str, JsonValue]] = []
            for invitation in invitations:
                if invitation.intended_identity_ref == principal or await _is_organization_admin(
                    self._service, principal, invitation.organization_id
                ):
                    visible_invitations.append(_invitation_resource(invitation))
            return tuple(visible_invitations)
        if self._collection == RESOURCE_OWNERSHIP_COLLECTION:
            visible_ownerships: list[dict[str, JsonValue]] = []
            for ownership_record in await self._service.repository.list_ownerships():
                if await self._service.resource_in_actor_scope(
                    actor_id=principal,
                    resource_type=ownership_record.resource_type,
                    resource_id=ownership_record.resource_id,
                ):
                    visible_ownerships.append(_ownership_resource(ownership_record))
            return tuple(visible_ownerships)
        if self._collection == RESOURCE_SHARE_COLLECTION:
            ownerships_by_id = {
                ownership.id: ownership
                for ownership in await self._service.repository.list_ownerships()
            }
            visible_shares: list[dict[str, JsonValue]] = []
            for share_record in await self._service.repository.list_all_shares():
                share_ownership = ownerships_by_id.get(share_record.ownership_id)
                if share_ownership is not None and await self._service.resource_in_actor_scope(
                    actor_id=principal,
                    resource_type=share_ownership.resource_type,
                    resource_id=share_ownership.resource_id,
                ):
                    visible_shares.append(_share_resource(share_record))
            return tuple(visible_shares)

        mappings = await self._service.repository.list_external_group_mappings()
        visible_mappings: list[dict[str, JsonValue]] = []
        for mapping in mappings:
            if await _is_organization_admin(self._service, principal, mapping.organization_id):
                visible_mappings.append(_external_group_mapping_resource(mapping))
        return tuple(visible_mappings)

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        """Enumerate actor-independent, privacy-minimal Search projections."""
        if self._collection == ORGANIZATION_COLLECTION:
            return tuple(
                _organization_search_resource(item)
                for item in await self._service.repository.list_organizations()
            )
        if self._collection == TEAM_COLLECTION:
            return tuple(
                _team_search_resource(item) for item in await self._service.repository.list_teams()
            )
        if self._collection == MEMBERSHIP_COLLECTION:
            return tuple(
                _membership_search_resource(item)
                for item in await self._service.repository.list_memberships()
            )
        return ()

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        principal = context.actor.principal_ref
        visible_orgs = await _visible_organization_ids(self._service, principal)

        if self._collection == ORGANIZATION_COLLECTION:
            organization = await _safe(self._service.repository.get_organization(resource_id))
            if organization.id not in visible_orgs:
                raise _hidden(resource_id)
            return _organization_resource(organization)
        if self._collection == TEAM_COLLECTION:
            team = await _safe(self._service.repository.get_team(resource_id))
            if team.organization_id not in visible_orgs:
                raise _hidden(resource_id)
            return _team_resource(team)
        if self._collection == MEMBERSHIP_COLLECTION:
            membership = await _safe(self._service.repository.get_membership(resource_id))
            if membership.organization_id not in visible_orgs:
                raise _hidden(resource_id)
            return _membership_resource(membership)
        if self._collection == INVITATION_COLLECTION:
            invitation = await _safe(self._service.repository.get_invitation(resource_id))
            if invitation.intended_identity_ref != principal and not await _is_organization_admin(
                self._service, principal, invitation.organization_id
            ):
                raise _hidden(resource_id)
            return _invitation_resource(invitation)
        if self._collection == RESOURCE_OWNERSHIP_COLLECTION:
            ownership_record = next(
                (
                    candidate
                    for candidate in await self._service.repository.list_ownerships()
                    if candidate.id == resource_id
                ),
                None,
            )
            if ownership_record is None or not await self._service.resource_in_actor_scope(
                actor_id=principal,
                resource_type=ownership_record.resource_type,
                resource_id=ownership_record.resource_id,
            ):
                raise _hidden(resource_id)
            return _ownership_resource(ownership_record)
        if self._collection == RESOURCE_SHARE_COLLECTION:
            share = await _safe(self._service.repository.get_share(resource_id))
            share_ownership = await _ownership_by_id(self._service, share.ownership_id)
            if share_ownership is None or not await self._service.resource_in_actor_scope(
                actor_id=principal,
                resource_type=share_ownership.resource_type,
                resource_id=share_ownership.resource_id,
            ):
                raise _hidden(resource_id)
            return _share_resource(share)

        mapping = next(
            (
                candidate
                for candidate in await self._service.repository.list_external_group_mappings()
                if candidate.id == resource_id
            ),
            None,
        )
        if mapping is None or not await _is_organization_admin(
            self._service, principal, mapping.organization_id
        ):
            raise _hidden(resource_id)
        return _external_group_mapping_resource(mapping)


class ControlPlane(_CurrentControlPlane):
    """Current Control Plane plus optional canonical organization collaboration APIs."""

    def __init__(
        self,
        *args: Any,
        organization_service: OrganizationService | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._organization_service = organization_service
        if organization_service is None:
            return
        for collection, service in organization_resource_services(organization_service).items():
            super().register_resource_service(collection, service)
        for command, handler in organization_command_handlers(organization_service).items():
            super().register_command(command, handler)

    @property
    def organization_service(self) -> OrganizationService | None:
        return self._organization_service

    def register_resource_service(self, collection: str, service: ResourceService) -> None:
        if collection in ORGANIZATION_COLLECTIONS:
            raise ValueError(
                f"extension collection conflicts with canonical organization route: {collection}"
            )
        super().register_resource_service(collection, service)

    def register_command(self, command: str, handler: CommandHandler) -> None:
        if command in ORGANIZATION_COMMANDS:
            raise ValueError(
                f"extension command conflicts with canonical organization command: {command}"
            )
        super().register_command(command, handler)

    async def execute_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        if command in ORGANIZATION_COMMANDS and self._organization_service is not None:
            scope = await _command_scope(
                self._organization_service, command, resource_ref, payload or {}
            )
            if scope is not None:
                await self._authorize(
                    context,
                    command,
                    resource_ref,
                    owner_type=scope[0],
                    owner_id=scope[1],
                )
        return await super().execute_command(context, command, resource_ref, payload)


def organization_resource_services(
    service: OrganizationService,
) -> dict[str, ResourceService]:
    return {
        collection: _OrganizationResources(service, cast(CollectionName, collection))
        for collection in ORGANIZATION_COLLECTIONS
    }


def organization_command_handlers(
    service: OrganizationService,
) -> dict[str, CommandHandler]:
    commands = _OrganizationCommands(service)
    return {
        "organization.create": commands.create_organization,
        "organization.archive": commands.archive_organization,
        "team.create": commands.create_team,
        "team.update": commands.update_team,
        "membership.add": commands.add_membership,
        "membership.assign": commands.assign_membership,
        "membership.suspend": commands.suspend_membership,
        "membership.remove": commands.remove_membership,
        "invitation.create": commands.create_invitation,
        "invitation.accept": commands.accept_invitation,
        "invitation.revoke": commands.revoke_invitation,
        "resource-ownership.set": commands.set_ownership,
        "resource-ownership.transfer": commands.transfer_ownership,
        "resource-share.create": commands.create_share,
        "resource-share.revoke": commands.revoke_share,
        "external-group-mapping.create": commands.create_external_group_mapping,
    }


class _OrganizationCommands:
    def __init__(self, service: OrganizationService) -> None:
        self._service = service

    async def create_organization(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        if resource_ref != ORGANIZATION_COLLECTION:
            raise _invalid("organization.create resource_ref must be 'organizations'")
        return _organization_resource(
            await self._service.create_organization(
                name=_required_string(payload, "name"),
                display_name=_optional_string(payload, "display_name"),
                owner_actor_id=context.actor.principal_ref,
                administrator_actor_ids=_string_tuple(payload, "administrator_actor_ids"),
            )
        )

    async def archive_organization(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del context
        _require_empty(payload)
        return _organization_resource(await _safe(self._service.archive_organization(resource_ref)))

    async def create_team(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del context
        return _team_resource(
            await _safe(
                self._service.create_team(
                    organization_id=resource_ref,
                    name=_required_string(payload, "name"),
                    description=_optional_string(payload, "description") or "",
                    parent_team_id=_optional_string(payload, "parent_team_id"),
                )
            )
        )

    async def update_team(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del context
        return _team_resource(
            await _safe(
                self._service.update_team(
                    resource_ref,
                    name=_optional_string(payload, "name"),
                    description=_optional_string(payload, "description"),
                )
            )
        )

    async def add_membership(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        return _membership_resource(
            await _safe(
                self._service.add_member(
                    actor_id=_required_string(payload, "actor_id"),
                    actor_type=_actor_type(payload.get("actor_type")),
                    organization_id=resource_ref,
                    team_id=_optional_string(payload, "team_id"),
                    role_refs=_string_tuple(payload, "role_refs"),
                    policy_refs=_string_tuple(payload, "policy_refs"),
                    created_by_actor_id=context.actor.principal_ref,
                )
            )
        )

    async def assign_membership(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del context
        return _membership_resource(
            await _safe(
                self._service.set_membership_assignments(
                    resource_ref,
                    role_refs=_string_tuple(payload, "role_refs"),
                    policy_refs=_string_tuple(payload, "policy_refs"),
                )
            )
        )

    async def suspend_membership(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del context
        _require_empty(payload)
        return _membership_resource(await _safe(self._service.suspend_member(resource_ref)))

    async def remove_membership(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del context
        _require_empty(payload)
        return _membership_resource(await _safe(self._service.remove_member(resource_ref)))

    async def create_invitation(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        return _invitation_resource(
            await _safe(
                self._service.invite_member(
                    organization_id=resource_ref,
                    invited_by_actor_id=context.actor.principal_ref,
                    expires_at=_timestamp(payload, "expires_at"),
                    team_id=_optional_string(payload, "team_id"),
                    intended_identity_ref=_optional_string(payload, "intended_identity_ref"),
                    intended_email_ref=_optional_string(payload, "intended_email_ref"),
                    role_refs=_string_tuple(payload, "role_refs"),
                    policy_refs=_string_tuple(payload, "policy_refs"),
                )
            )
        )

    async def accept_invitation(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        _require_empty(payload)
        return _membership_resource(
            await _safe(
                self._service.accept_invitation(
                    resource_ref,
                    actor_id=context.actor.principal_ref,
                    actor_type=_context_actor_type(context),
                )
            )
        )

    async def revoke_invitation(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del context
        _require_empty(payload)
        return _invitation_resource(await _safe(self._service.revoke_invitation(resource_ref)))

    async def set_ownership(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        resource_id = _matching_resource_id(resource_ref, payload)
        return _ownership_resource(
            await _safe(
                self._service.set_resource_owner(
                    resource_type=_required_string(payload, "resource_type"),
                    resource_id=resource_id,
                    owner_ref=_owner_ref(payload, "owner_ref"),
                    organization_id=_optional_string(payload, "organization_id"),
                    created_by_actor_id=context.actor.principal_ref,
                )
            )
        )

    async def transfer_ownership(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del context
        resource_id = _matching_resource_id(resource_ref, payload)
        return _ownership_resource(
            await _safe(
                self._service.transfer_resource(
                    resource_type=_required_string(payload, "resource_type"),
                    resource_id=resource_id,
                    new_owner_ref=_owner_ref(payload, "owner_ref"),
                    organization_id=_optional_string(payload, "organization_id"),
                )
            )
        )

    async def create_share(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        resource_id = _matching_resource_id(resource_ref, payload)
        return _share_resource(
            await _safe(
                self._service.share_resource(
                    resource_type=_required_string(payload, "resource_type"),
                    resource_id=resource_id,
                    target_ref=_owner_ref(payload, "target_ref"),
                    granted_by_actor_id=context.actor.principal_ref,
                    policy_refs=_string_tuple(payload, "policy_refs"),
                    allow_cross_organization=_boolean(payload, "allow_cross_organization", False),
                )
            )
        )

    async def revoke_share(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del context
        _require_empty(payload)
        return _share_resource(await _safe(self._service.revoke_share(resource_ref)))

    async def create_external_group_mapping(
        self, context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del context
        return _external_group_mapping_resource(
            await _safe(
                self._service.add_external_group_mapping(
                    provider_ref=_required_string(payload, "provider_ref"),
                    external_group_id=_required_string(payload, "external_group_id"),
                    organization_id=resource_ref,
                    team_id=_optional_string(payload, "team_id"),
                    provisioning_mode=_optional_string(payload, "provisioning_mode") or "manual",
                )
            )
        )


async def _command_scope(
    service: OrganizationService,
    command: str,
    resource_ref: str,
    payload: dict[str, JsonValue],
) -> tuple[ScopeType, str] | None:
    if command in {"organization.create", "invitation.accept"}:
        return None
    if command in {
        "organization.archive",
        "team.create",
        "membership.add",
        "invitation.create",
        "external-group-mapping.create",
    }:
        return ("organization", resource_ref)
    if command == "team.update":
        team = await _safe(service.repository.get_team(resource_ref))
        return ("team", team.id)
    if command in {"membership.assign", "membership.suspend", "membership.remove"}:
        membership = await _safe(service.repository.get_membership(resource_ref))
        return (
            ("team", membership.team_id)
            if membership.team_id is not None
            else ("organization", membership.organization_id)
        )
    if command == "invitation.revoke":
        invitation = await _safe(service.repository.get_invitation(resource_ref))
        return (
            ("team", invitation.team_id)
            if invitation.team_id is not None
            else ("organization", invitation.organization_id)
        )
    if command in {"resource-ownership.set", "resource-ownership.transfer"}:
        owner_scope = _owner_scope(_owner_ref(payload, "owner_ref"))
        if owner_scope is not None:
            return owner_scope
        organization_id = _optional_string(payload, "organization_id")
        return None if organization_id is None else ("organization", organization_id)
    if command == "resource-share.create":
        ownership = await _safe(
            service.repository.get_ownership(
                _required_string(payload, "resource_type"),
                _required_string(payload, "resource_id"),
            )
        )
        return _ownership_scope(ownership)
    if command == "resource-share.revoke":
        share = await _safe(service.repository.get_share(resource_ref))
        share_ownership = await _ownership_by_id(service, share.ownership_id)
        if share_ownership is None:
            raise _hidden(resource_ref)
        return _ownership_scope(share_ownership)
    return None


async def _visible_organization_ids(
    service: OrganizationService, principal_ref: str
) -> frozenset[str]:
    visible: set[str] = set()
    for organization in await service.repository.list_organizations():
        if await service.actor_can_discover_organization(
            actor_id=principal_ref, organization_id=organization.id
        ):
            visible.add(organization.id)
    return frozenset(visible)


async def _is_organization_admin(
    service: OrganizationService, principal_ref: str, organization_id: str
) -> bool:
    try:
        organization = await service.repository.get_organization(organization_id)
    except LookupError:
        return False
    return (
        principal_ref == organization.owner_actor_id
        or principal_ref in organization.administrator_actor_ids
    )


async def _ownership_by_id(
    service: OrganizationService, ownership_id: str
) -> ResourceOwnership | None:
    return next(
        (item for item in await service.repository.list_ownerships() if item.id == ownership_id),
        None,
    )


def _ownership_scope(ownership: ResourceOwnership) -> tuple[ScopeType, str] | None:
    direct = _owner_scope(ownership.owner_ref)
    if direct is not None:
        return direct
    if ownership.organization_id is not None:
        return ("organization", ownership.organization_id)
    return None


def _owner_scope(owner_ref: OwnerRef) -> tuple[ScopeType, str] | None:
    if owner_ref.type == "organization":
        return ("organization", owner_ref.id)
    if owner_ref.type == "team":
        return ("team", owner_ref.id)
    return None


async def _safe(awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except LookupError as exc:
        raise ContractError(ErrorCode.NOT_FOUND, str(exc)) from exc


def _organization_resource(value: Organization) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "type": "organization",
        "name": value.name,
        "display_name": value.display_name,
        "status": value.status.value,
        "owner_actor_id": value.owner_actor_id,
        "administrator_actor_ids": list(value.administrator_actor_ids),
        "settings": dict(value.settings),
        "default_policy_refs": list(value.default_policy_refs),
        "default_configuration_refs": list(value.default_configuration_refs),
        "provenance": dict(value.provenance),
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
        "archived_at": _time_json(value.archived_at),
    }


def _team_resource(value: Team) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "type": "team",
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
        "archived_at": _time_json(value.archived_at),
    }


def _membership_resource(value: Membership) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "type": "membership",
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
        "suspended_at": _time_json(value.suspended_at),
        "revoked_at": _time_json(value.revoked_at),
        "expires_at": _time_json(value.expires_at),
    }


def _organization_search_resource(value: Organization) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "type": "organization",
        "name": value.name,
        "display_name": value.display_name,
        "status": value.status.value,
        "organization_id": value.id,
        "owner_type": "organization",
        "owner_id": value.id,
        "updated_at": value.updated_at.isoformat(),
    }


def _team_search_resource(value: Team) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "type": "team",
        "organization_id": value.organization_id,
        "name": value.name,
        "description": value.description,
        "status": value.status.value,
        "parent_team_id": value.parent_team_id,
        "owner_type": "organization",
        "owner_id": value.organization_id,
        "updated_at": value.updated_at.isoformat(),
    }


def _membership_search_resource(value: Membership) -> dict[str, JsonValue]:
    updated_at = value.revoked_at or value.suspended_at or value.accepted_at
    return {
        "id": value.id,
        "type": "membership",
        "organization_id": value.organization_id,
        "team_id": value.team_id,
        "actor_id": value.actor_id,
        "actor_type": value.actor_type.value,
        "status": value.status.value,
        "owner_type": "organization",
        "owner_id": value.organization_id,
        "updated_at": updated_at.isoformat(),
    }


def _invitation_resource(value: Invitation) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "type": "invitation",
        "organization_id": value.organization_id,
        "team_id": value.team_id,
        "intended_identity_ref": value.intended_identity_ref,
        "intended_email_ref": value.intended_email_ref,
        "invited_by_actor_id": value.invited_by_actor_id,
        "requested_role_refs": list(value.requested_role_refs),
        "requested_policy_refs": list(value.requested_policy_refs),
        "status": value.status.value,
        "created_at": value.created_at.isoformat(),
        "expires_at": value.expires_at.isoformat(),
        "accepted_at": _time_json(value.accepted_at),
        "revoked_at": _time_json(value.revoked_at),
    }


def _ownership_resource(value: ResourceOwnership) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "type": "resource-ownership",
        "resource_type": value.resource_type,
        "resource_id": value.resource_id,
        "owner_type": value.owner_ref.type,
        "owner_id": value.owner_ref.id,
        "organization_id": value.organization_id,
        "created_by_actor_id": value.created_by_actor_id,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
    }


def _share_resource(value: ResourceShare) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "type": "resource-share",
        "ownership_id": value.ownership_id,
        "target_type": value.target_ref.type,
        "target_id": value.target_ref.id,
        "granted_by_actor_id": value.granted_by_actor_id,
        "organization_id": value.organization_id,
        "status": value.status.value,
        "policy_refs": list(value.policy_refs),
        "created_at": value.created_at.isoformat(),
        "revoked_at": _time_json(value.revoked_at),
    }


def _external_group_mapping_resource(value: ExternalGroupMapping) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "type": "external-group-mapping",
        "provider_ref": value.provider_ref,
        "external_group_id": value.external_group_id,
        "organization_id": value.organization_id,
        "team_id": value.team_id,
        "provisioning_mode": value.provisioning_mode,
        "active": value.active,
        "created_at": value.created_at.isoformat(),
    }


def _required_string(payload: dict[str, JsonValue], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{field} must be a non-blank string")
    return value


def _optional_string(payload: dict[str, JsonValue], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{field} must be a non-blank string when provided")
    return value


def _string_tuple(payload: dict[str, JsonValue], field: str) -> tuple[str, ...]:
    value = payload.get(field)
    if value is None:
        return ()
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise _invalid(f"{field} must be an array of non-blank strings")
    return tuple(cast(list[str], value))


def _timestamp(payload: dict[str, JsonValue], field: str) -> datetime:
    raw = _required_string(payload, field)
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise _invalid(f"{field} must be an ISO-8601 timestamp") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise _invalid(f"{field} must be timezone-aware")
    return value


def _actor_type(value: JsonValue | None) -> ActorType:
    if value is None:
        return ActorType.HUMAN
    if not isinstance(value, str):
        raise _invalid("actor_type must be a string")
    try:
        return ActorType(value)
    except ValueError as exc:
        raise _invalid(f"unsupported actor_type: {value}") from exc


def _context_actor_type(context: RequestContext) -> ActorType:
    value = context.actor.actor_type
    if value is None:
        return ActorType.HUMAN
    try:
        return ActorType(value)
    except ValueError as exc:
        raise _invalid(f"unsupported authenticated actor_type: {value}") from exc


def _owner_ref(payload: dict[str, JsonValue], field: str) -> OwnerRef:
    value = payload.get(field)
    if not isinstance(value, dict):
        raise _invalid(f"{field} must be an object")
    owner_type = value.get("type")
    owner_id = value.get("id")
    if not isinstance(owner_type, str) or owner_type not in {
        "user",
        "organization",
        "team",
        "service",
    }:
        raise _invalid(f"{field}.type is invalid")
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise _invalid(f"{field}.id must be a non-blank string")
    return OwnerRef(type=cast(OwnerKind, owner_type), id=owner_id)


def _matching_resource_id(resource_ref: str, payload: dict[str, JsonValue]) -> str:
    resource_id = _required_string(payload, "resource_id")
    if resource_ref != resource_id:
        raise _invalid("resource_ref must match resource_id")
    return resource_id


def _boolean(payload: dict[str, JsonValue], field: str, default: bool) -> bool:
    value = payload.get(field)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise _invalid(f"{field} must be a boolean")
    return value


def _require_empty(payload: dict[str, JsonValue]) -> None:
    if payload:
        raise _invalid("command does not accept payload fields")


def _time_json(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _invalid(message: str) -> ContractError:
    return ContractError(ErrorCode.INVALID_REQUEST, message)


def _hidden(resource_id: str) -> ContractError:
    return ContractError(ErrorCode.NOT_FOUND, f"organization resource not found: {resource_id}")
