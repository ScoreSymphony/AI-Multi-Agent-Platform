"""Explicit canonical Organization/Accounting Control Plane composition (#87, #982).

Organization collaboration resources and commands, plus optional Accounting projections,
are published through named ``ControlPlaneModule`` owners. Cross-domain ownership
mirroring remains an integration concern of this façade, but command dispatch and
northbound ownership no longer depend on a later-domain ``execute_command`` MRO layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.organizations import OrganizationService, ResourceOwnership
from ai_multi_agent_platform.search import SearchResult

from .conversation_current_composition import ControlPlane as _CurrentControlPlane
from .extensions import CommandAuthorizer, ControlPlaneModule, ResourceService
from .models import OwnerType, RequestContext
from .module_registry import install_control_plane_modules
from .organization_api import (
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
from .service import _payload_digest, _resolve_owner

if TYPE_CHECKING:
    from ai_multi_agent_platform.accounting.service import AccountingService

ORGANIZATION_RUNTIME_COMMANDS = ORGANIZATION_COMMANDS + ORGANIZATION_MANAGEMENT_COMMANDS
ORGANIZATION_MODULE = "organizations"
ACCOUNTING_MODULE = "accounting"

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
    """Current runtime plus explicitly owned Organization and Accounting modules."""

    def __init__(
        self,
        *args: Any,
        organization_service: OrganizationService | None = None,
        accounting_service: AccountingService | None = None,
        **kwargs: Any,
    ) -> None:
        # #75 consumes the same AccountingService for threshold attention. Preserve
        # that single runtime instance throughout the composition chain.
        super().__init__(*args, accounting_service=accounting_service, **kwargs)
        self._organization_service = organization_service
        self._accounting_service = accounting_service
        self._ownership_mirror = (
            None if organization_service is None else CanonicalOwnershipMirror(organization_service)
        )

        modules: list[ControlPlaneModule] = []
        accounting_module = self._accounting_control_plane_module()
        if accounting_module is not None:
            modules.append(accounting_module)
        organization_module = self._organization_control_plane_module()
        if organization_module is not None:
            modules.append(organization_module)
        install_control_plane_modules(self, tuple(modules))

    def _accounting_control_plane_module(self) -> ControlPlaneModule | None:
        accounting = self._accounting_service
        if accounting is None:
            return None
        services: Mapping[str, ResourceService]
        if self._organization_service is None:
            from ai_multi_agent_platform.accounting.control_plane import (
                accounting_resource_services,
            )

            services = accounting_resource_services(accounting)
        else:
            # Local import keeps organizations.__init__ free from the
            # control_plane/accounting cycle.
            from ai_multi_agent_platform.organizations.accounting import (
                organization_accounting_resource_services,
            )

            services = organization_accounting_resource_services(
                accounting,
                self._organization_service,
            )
        return ControlPlaneModule(
            name=ACCOUNTING_MODULE,
            resource_services=services,
        )

    def _organization_control_plane_module(self) -> ControlPlaneModule | None:
        service = self._organization_service
        mirror = self._ownership_mirror
        if service is None or mirror is None:
            return None

        resources = organization_resource_services(service)
        for collection in (RESOURCE_OWNERSHIP_COLLECTION, RESOURCE_SHARE_COLLECTION):
            resources[collection] = AdministrativeOwnershipVisibility(
                service,
                collection,
                resources[collection],
            )

        handlers = {
            **organization_command_handlers(service),
            **organization_management_command_handlers(service),
        }

        def authorizer(command: str) -> CommandAuthorizer:
            async def authorize(
                context: RequestContext,
                resource_ref: str,
                payload: dict[str, JsonValue],
            ) -> None:
                reject_direct_mirror_owner_mutation(command, payload)
                scope: tuple[OwnerType, str] | None = None
                if command in ORGANIZATION_COMMANDS:
                    scope = await _command_scope(service, command, resource_ref, payload)
                elif command in ORGANIZATION_MANAGEMENT_COMMANDS:
                    scope = await organization_management_command_scope(
                        service,
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
                        service,
                        command,
                        payload,
                    )
                    if cross_organization_target is not None:
                        await self._authorize(
                            context,
                            "resource-share.cross-organization",
                            cross_organization_target,
                            owner_type=scope[0],
                            owner_id=scope[1],
                        )
                await self._authorize(
                    context,
                    command,
                    resource_ref,
                    request_payload_digest=_payload_digest(payload),
                )

            return authorize

        async def mirror_command(
            context: RequestContext,
            command: str,
            resource_ref: str,
            result: dict[str, JsonValue],
        ) -> None:
            del resource_ref
            await _mirror_command_resource(mirror, context, command, result)

        return ControlPlaneModule(
            name=ORGANIZATION_MODULE,
            resource_services=resources,
            command_handlers=handlers,
            command_authorizers={command: authorizer(command) for command in handlers},
            command_observers=(mirror_command,),
        )

    @property
    def organization_service(self) -> OrganizationService | None:
        return self._organization_service

    @property
    def accounting_service(self) -> AccountingService | None:
        return self._accounting_service

    @property
    def ownership_mirror(self) -> CanonicalOwnershipMirror | None:
        return self._ownership_mirror

    @property
    def organization_search_visibility_available(self) -> bool:
        return self._organization_service is not None

    async def _search_result_allowed(
        self,
        context: RequestContext,
        result: SearchResult,
    ) -> bool:
        organization_id = _search_result_organization_id(result)
        if organization_id is not None and self._organization_service is not None:
            if not await self._organization_service.actor_can_discover_organization(
                actor_id=context.actor.principal_ref,
                organization_id=organization_id,
            ):
                return False
        return await super()._search_result_allowed(context, result)

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


def _search_result_organization_id(result: SearchResult) -> str | None:
    value = result.provenance.get("organization_id")
    return value if isinstance(value, str) and value else None


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


__all__ = [
    "ACCOUNTING_MODULE",
    "ControlPlane",
    "ORGANIZATION_MODULE",
    "ORGANIZATION_RUNTIME_COMMANDS",
]
