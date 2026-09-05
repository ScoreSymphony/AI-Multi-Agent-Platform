"""Focused organization-management commands that complete issue #87 lifecycle surfaces."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.organizations import (
    ExternalGroupMapping,
    MembershipStatus,
    OrganizationService,
    OrganizationStatus,
    TeamStatus,
)

from .extensions import CommandHandler
from .models import OwnerType, RequestContext
from .organization_api import (
    _external_group_mapping_resource,
    _membership_resource,
    _organization_resource,
    _team_resource,
)

ORGANIZATION_MANAGEMENT_COMMANDS = (
    "organization.update",
    "organization.owner.transfer",
    "team.configure",
    "membership.leave",
    "external-group-mapping.deactivate",
)


class OrganizationManagementCommands:
    def __init__(self, service: OrganizationService) -> None:
        self._service = service

    async def update_organization(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        organization = await self._service.repository.get_organization(resource_ref)
        if organization.status is not OrganizationStatus.ACTIVE:
            raise ValueError("archived organizations cannot be updated")
        display_name = organization.display_name
        if "display_name" in payload:
            raw_display_name = payload["display_name"]
            if raw_display_name is not None and (
                not isinstance(raw_display_name, str) or not raw_display_name.strip()
            ):
                raise ValueError("display_name must be null or a non-blank string")
            display_name = raw_display_name
        updated = replace(
            organization,
            name=_optional_string(payload, "name") or organization.name,
            display_name=display_name,
            administrator_actor_ids=_optional_string_tuple(
                payload,
                "administrator_actor_ids",
                organization.administrator_actor_ids,
            ),
            settings=_optional_object(payload, "settings", organization.settings),
            default_policy_refs=_optional_string_tuple(
                payload,
                "default_policy_refs",
                organization.default_policy_refs,
            ),
            default_configuration_refs=_optional_string_tuple(
                payload,
                "default_configuration_refs",
                organization.default_configuration_refs,
            ),
            updated_at=datetime.now(UTC),
        )
        return _organization_resource(await self._service.repository.save_organization(updated))

    async def transfer_organization_owner(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if set(payload) != {"new_owner_actor_id"}:
            raise ValueError("organization.owner.transfer requires only new_owner_actor_id")
        new_owner_actor_id = _required_string(payload, "new_owner_actor_id")
        organization = await self._service.repository.get_organization(resource_ref)
        if organization.status is not OrganizationStatus.ACTIVE:
            raise ValueError("archived organizations cannot transfer ownership")
        if organization.owner_actor_id == new_owner_actor_id:
            return _organization_resource(organization)
        if context.actor.principal_ref != organization.owner_actor_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "only the current organization owner can transfer ownership",
            )
        memberships = await self._service.repository.list_memberships(
            actor_id=new_owner_actor_id,
            organization_id=organization.id,
        )
        if not any(item.status is MembershipStatus.ACTIVE for item in memberships):
            raise ValueError("new organization owner must have an active membership")
        updated = replace(
            organization,
            owner_actor_id=new_owner_actor_id,
            updated_at=datetime.now(UTC),
        )
        return _organization_resource(await self._service.repository.save_organization(updated))

    async def configure_team(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        team = await self._service.repository.get_team(resource_ref)
        if team.status is not TeamStatus.ACTIVE:
            raise ValueError("archived teams cannot be configured")
        parent_team_id = team.parent_team_id
        if "parent_team_id" in payload:
            raw_parent = payload["parent_team_id"]
            if raw_parent is not None and (
                not isinstance(raw_parent, str) or not raw_parent.strip()
            ):
                raise ValueError("parent_team_id must be null or a non-blank string")
            parent_team_id = raw_parent
            if parent_team_id == team.id:
                raise ValueError("team cannot be its own parent")
            if parent_team_id is not None:
                parent = await self._service.repository.get_team(parent_team_id)
                if parent.organization_id != team.organization_id:
                    raise ValueError("parent team must belong to the same organization")
                if parent.status is not TeamStatus.ACTIVE:
                    raise ValueError("parent team must be active")
        updated = replace(
            team,
            name=_optional_string(payload, "name") or team.name,
            description=(
                team.description
                if "description" not in payload
                else _required_string(payload, "description", allow_blank=True)
            ),
            parent_team_id=parent_team_id,
            project_scope_refs=_optional_string_tuple(
                payload,
                "project_scope_refs",
                team.project_scope_refs,
            ),
            default_policy_refs=_optional_string_tuple(
                payload,
                "default_policy_refs",
                team.default_policy_refs,
            ),
            default_configuration_refs=_optional_string_tuple(
                payload,
                "default_configuration_refs",
                team.default_configuration_refs,
            ),
            updated_at=datetime.now(UTC),
        )
        return _team_resource(await self._service.repository.save_team(updated))

    async def leave_membership(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if payload:
            raise ValueError("membership.leave does not accept a payload")
        membership = await self._service.leave_scope(
            resource_ref,
            actor_id=context.actor.principal_ref,
        )
        return _membership_resource(membership)

    async def deactivate_external_group_mapping(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        if payload:
            raise ValueError("external-group-mapping.deactivate does not accept a payload")
        mapping = await _external_group_mapping(self._service, resource_ref)
        if not mapping.active:
            return _external_group_mapping_resource(mapping)
        updated = replace(mapping, active=False)
        saved = await self._service.repository.save_external_group_mapping(updated)
        return _external_group_mapping_resource(saved)


def organization_management_command_handlers(
    service: OrganizationService,
) -> dict[str, CommandHandler]:
    commands = OrganizationManagementCommands(service)
    return {
        "organization.update": commands.update_organization,
        "organization.owner.transfer": commands.transfer_organization_owner,
        "team.configure": commands.configure_team,
        "membership.leave": commands.leave_membership,
        "external-group-mapping.deactivate": commands.deactivate_external_group_mapping,
    }


async def organization_management_command_scope(
    service: OrganizationService,
    command: str,
    resource_ref: str,
) -> tuple[OwnerType, str] | None:
    if command in {"organization.update", "organization.owner.transfer"}:
        organization = await service.repository.get_organization(resource_ref)
        return ("organization", organization.id)
    if command == "team.configure":
        team = await service.repository.get_team(resource_ref)
        return ("team", team.id)
    if command == "membership.leave":
        membership = await service.repository.get_membership(resource_ref)
        if membership.team_id is not None:
            return ("team", membership.team_id)
        return ("organization", membership.organization_id)
    if command == "external-group-mapping.deactivate":
        mapping = await _external_group_mapping(service, resource_ref)
        if mapping.team_id is not None:
            return ("team", mapping.team_id)
        return ("organization", mapping.organization_id)
    return None


async def _external_group_mapping(
    service: OrganizationService,
    mapping_id: str,
) -> ExternalGroupMapping:
    for mapping in await service.repository.list_external_group_mappings():
        if mapping.id == mapping_id:
            return mapping
    raise ContractError(ErrorCode.NOT_FOUND, f"external group mapping not found: {mapping_id}")


def _optional_string(payload: dict[str, JsonValue], name: str) -> str | None:
    if name not in payload:
        return None
    return _required_string(payload, name)


def _required_string(
    payload: dict[str, JsonValue],
    name: str,
    *,
    allow_blank: bool = False,
) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or (not allow_blank and not value.strip()):
        requirement = "a string" if allow_blank else "a non-blank string"
        raise ValueError(f"{name} must be {requirement}")
    return value


def _optional_string_tuple(
    payload: dict[str, JsonValue],
    name: str,
    current: tuple[str, ...],
) -> tuple[str, ...]:
    if name not in payload:
        return current
    value = payload[name]
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array of non-blank strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name} must be an array of non-blank strings")
        items.append(item)
    if len(items) != len(set(items)):
        raise ValueError(f"{name} must not contain duplicates")
    return tuple(items)


def _optional_object(
    payload: dict[str, JsonValue],
    name: str,
    current: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    if name not in payload:
        return current
    value = payload[name]
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return dict(value)
