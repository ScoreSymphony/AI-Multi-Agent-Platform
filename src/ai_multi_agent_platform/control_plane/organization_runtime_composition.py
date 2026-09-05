"""Current runtime Control Plane composed with canonical organization management."""

from __future__ import annotations

from typing import Any, Literal, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.organizations import OrganizationService, ResourceOwnership

from .conversation_current_composition import ControlPlane as _CurrentControlPlane
from .extensions import CommandHandler, ResourceService
from .models import OwnerType, RequestContext
from .organization_api import (
    ORGANIZATION_COLLECTIONS,
    ORGANIZATION_COMMANDS,
    RESOURCE_OWNERSHIP_COLLECTION,
    RESOURCE_SHARE_COLLECTION,
    _command_scope,
    organization_command_handlers,
    organization_resource_services,
)
from .organization_management import (
    ORGANIZATION_MANAGEMENT_COMMANDS,
    organization_management_command_handlers,
    organization_management_command_scope,
)
from .organization_ownership_integration import (
    CanonicalOwnershipMirror,
    reject_direct_mirror_owner_mutation,
)
from .organization_visibility import AdministrativeOwnershipVisibility
from .service import _resolve_owner

ORGANIZATION_RUNTIME_COMMANDS = ORGANIZATION_COMMANDS + ORGANIZATION_MANAGEMENT_COMMANDS

_CANONICAL_OWNER_COMMAND_TYPES = {
    "agent.create": "agent",
    "agent.update": "agent",
    "agent.clone": "agent",
    "agent.rollback": "agent",
    "agent-team.create": "agent_team",
    "agent-team.update": "agent_team",
    "agent-team.clone": "agent_team",
    "agent-team.rollback": "agent_team",
    "automation.create": "automation",
    "automation.update": "automation",
    "automation.pause": "automation",
    "automation.resume": "automation",
    "automation.disable": "automation",
    "memory.create": "memory",
    "memory.promote": "memory",
    "memory.update": "memory",
    "knowledge.register": "knowledge_source",
    "knowledge.update": "knowledge_source",
    "connection.create": "connection",
    "connection.enable": "connection",
    "connection.disable": "connection",
    "connection.health": "connection",
}
_STRICT_DATA_OWNER_RESOURCE_TYPES = frozenset({"memory", "knowledge_source"})
_STRICT_STRUCTURED_OWNER_RESOURCE_TYPES = frozenset({"connection"})
_STRICT_COMMAND_OWNER_RESOURCE_TYPES = (
    _STRICT_DATA_OWNER_RESOURCE_TYPES | _STRICT_STRUCTURED_OWNER_RESOURCE_TYPES
)


class ControlPlane(_CurrentControlPlane):
    """Current runtime composition plus canonical organization collaboration APIs."""

    def __init__(
        self,
        *args: Any,
        organization_service: OrganizationService | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._organization_service = organization_service
        self._ownership_mirror = (
            None if organization_service is None else CanonicalOwnershipMirror(organization_service)
        )
        if organization_service is None:
            return
        for collection, resource_service in organization_resource_services(
            organization_service
        ).items():
            if collection in {RESOURCE_OWNERSHIP_COLLECTION, RESOURCE_SHARE_COLLECTION}:
                resource_service = AdministrativeOwnershipVisibility(
                    organization_service,
                    collection,
                    resource_service,
                )
            super().register_resource_service(collection, resource_service)
        for command, handler in organization_command_handlers(organization_service).items():
            super().register_command(command, handler)
        for command, handler in organization_management_command_handlers(
            organization_service
        ).items():
            super().register_command(command, handler)

    @property
    def organization_service(self) -> OrganizationService | None:
        return self._organization_service

    @property
    def ownership_mirror(self) -> CanonicalOwnershipMirror | None:
        return self._ownership_mirror

    def register_resource_service(self, collection: str, service: ResourceService) -> None:
        if collection in ORGANIZATION_COLLECTIONS:
            raise ValueError(
                f"extension collection conflicts with canonical organization route: {collection}"
            )
        super().register_resource_service(collection, service)

    def register_command(self, command: str, handler: CommandHandler) -> None:
        if command in ORGANIZATION_RUNTIME_COMMANDS:
            raise ValueError(
                f"extension command conflicts with canonical organization command: {command}"
            )
        super().register_command(command, handler)

    async def create_project(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if self._ownership_mirror is not None:
            owner_type, owner_id = _resolve_owner(context.actor, payload)
            await self._ownership_mirror.validate_owner(OwnerRef(type=owner_type, id=owner_id))
        resource = await super().create_project(context, payload)
        if self._ownership_mirror is None:
            return resource
        project_id = _resource_id(resource, "project")
        project = self.scopes.get_project(project_id)
        await self._ownership_mirror.mirror(
            resource_type="project",
            resource_id=project.id,
            owner_ref=project.owner_ref,
            actor_ref=context.actor.principal_ref,
        )
        return resource

    async def create_workspace(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        resource = await super().create_workspace(context, payload)
        if self._ownership_mirror is None:
            return resource
        workspace_id = _resource_id(resource, "workspace")
        workspace = self.scopes.get_workspace(workspace_id)
        project = self.scopes.get_project(workspace.project_id)
        await self._ownership_mirror.mirror(
            resource_type="project",
            resource_id=project.id,
            owner_ref=project.owner_ref,
            actor_ref=context.actor.principal_ref,
        )
        await self._ownership_mirror.mirror(
            resource_type="workspace",
            resource_id=workspace.id,
            owner_ref=OwnerRef(type=workspace.owner_type, id=workspace.owner_id),
            actor_ref=context.actor.principal_ref,
        )
        return resource

    async def execute_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        effective_payload = payload or {}
        if self._ownership_mirror is not None:
            reject_direct_mirror_owner_mutation(command, effective_payload)
        if self._organization_service is not None:
            scope: tuple[OwnerType, str] | None = None
            if command in ORGANIZATION_COMMANDS:
                scope = await _command_scope(
                    self._organization_service,
                    command,
                    resource_ref,
                    effective_payload,
                )
            elif command in ORGANIZATION_MANAGEMENT_COMMANDS:
                scope = await organization_management_command_scope(
                    self._organization_service,
                    command,
                    resource_ref,
                )
            if scope is not None:
                await self._authorize(
                    context,
                    command,
                    resource_ref,
                    owner_type=scope[0],
                    owner_id=scope[1],
                )
                cross_organization_target = await _cross_organization_share_target(
                    self._organization_service,
                    command,
                    effective_payload,
                )
                if cross_organization_target is not None:
                    await self._authorize(
                        context,
                        "resource-share.cross-organization",
                        cross_organization_target,
                        owner_type=scope[0],
                        owner_id=scope[1],
                    )
        resource = await super().execute_command(context, command, resource_ref, effective_payload)
        if self._ownership_mirror is not None:
            await _mirror_command_resource(
                self._ownership_mirror,
                context,
                command,
                resource,
            )
        return resource


async def _cross_organization_share_target(
    service: OrganizationService,
    command: str,
    payload: dict[str, JsonValue],
) -> str | None:
    if command != "resource-share.create" or payload.get("allow_cross_organization") is not True:
        return None
    resource_type = payload.get("resource_type")
    resource_id = payload.get("resource_id")
    target = payload.get("target_ref")
    if not isinstance(resource_type, str) or not isinstance(resource_id, str):
        return None
    if not isinstance(target, dict):
        return None
    target_type = target.get("type")
    target_id = target.get("id")
    if target_type not in {"organization", "team"} or not isinstance(target_id, str):
        return None

    try:
        ownership = await service.repository.get_ownership(resource_type, resource_id)
    except LookupError:
        return None
    source_organization_id = await _ownership_organization_id(service, ownership)
    target_organization_id = await _target_organization_id(service, target_type, target_id)
    if (
        source_organization_id is None
        or target_organization_id is None
        or source_organization_id == target_organization_id
    ):
        return None
    return f"{target_type}:{target_id}"


async def _ownership_organization_id(
    service: OrganizationService,
    ownership: ResourceOwnership,
) -> str | None:
    if ownership.organization_id is not None:
        return ownership.organization_id
    if ownership.owner_ref.type == "organization":
        return ownership.owner_ref.id
    if ownership.owner_ref.type == "team":
        try:
            team = await service.repository.get_team(ownership.owner_ref.id)
        except LookupError:
            return None
        return team.organization_id
    return None


async def _target_organization_id(
    service: OrganizationService,
    target_type: object,
    target_id: str,
) -> str | None:
    if target_type == "organization":
        try:
            await service.repository.get_organization(target_id)
        except LookupError:
            return None
        return target_id
    if target_type == "team":
        try:
            team = await service.repository.get_team(target_id)
        except LookupError:
            return None
        return team.organization_id
    return None


async def _mirror_command_resource(
    mirror: CanonicalOwnershipMirror,
    context: RequestContext,
    command: str,
    resource: dict[str, JsonValue],
) -> None:
    resource_type = _CANONICAL_OWNER_COMMAND_TYPES.get(command)
    if resource_type is None:
        return
    resource_id = _resource_id(resource, resource_type)
    if resource_type == "automation":
        identity = resource.get("identity")
        if not isinstance(identity, dict):
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "canonical automation response is missing its identity owner",
            )
        owner_ref = _owner_ref(identity.get("owner_type"), identity.get("owner_id"))
    elif resource_type in _STRICT_DATA_OWNER_RESOURCE_TYPES:
        owner_ref = _principal_owner_ref(resource.get("owner_ref"), resource_type)
    elif resource_type in _STRICT_STRUCTURED_OWNER_RESOURCE_TYPES:
        owner_ref = _owner_ref(resource.get("owner_type"), resource.get("owner_id"))
    else:
        raw_owner = resource.get("owner_ref")
        if not isinstance(raw_owner, dict):
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"canonical {resource_type} response is missing owner_ref",
            )
        owner_ref = _owner_ref(raw_owner.get("type"), raw_owner.get("id"))

    if resource_type in _STRICT_COMMAND_OWNER_RESOURCE_TYPES:
        await mirror.mirror(
            resource_type=resource_type,
            resource_id=resource_id,
            owner_ref=owner_ref,
            actor_ref=context.actor.principal_ref,
        )
        return
    await mirror.mirror_authoritative(
        resource_type=resource_type,
        resource_id=resource_id,
        owner_ref=owner_ref,
        actor_ref=context.actor.principal_ref,
    )


def _principal_owner_ref(raw_owner: JsonValue | None, resource_type: str) -> OwnerRef:
    if not isinstance(raw_owner, str) or ":" not in raw_owner:
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            f"canonical {resource_type} response is missing a canonical owner_ref",
        )
    raw_type, raw_id = raw_owner.split(":", 1)
    return _owner_ref(raw_type, raw_id)


def _owner_ref(raw_type: JsonValue | None, raw_id: JsonValue | None) -> OwnerRef:
    if raw_type not in {"user", "organization", "team", "service"}:
        raise ContractError(ErrorCode.BACKEND_ERROR, "canonical resource owner type is invalid")
    if not isinstance(raw_id, str) or not raw_id:
        raise ContractError(ErrorCode.BACKEND_ERROR, "canonical resource owner id is missing")
    owner_type = cast(Literal["user", "organization", "team", "service"], raw_type)
    return OwnerRef(type=owner_type, id=raw_id)


def _resource_id(resource: dict[str, JsonValue], expected_type: str) -> str:
    resource_id = resource.get("id")
    if isinstance(resource_id, str) and resource_id:
        return resource_id
    raise ContractError(
        ErrorCode.BACKEND_ERROR,
        f"canonical {expected_type} response is missing its resource id",
    )
