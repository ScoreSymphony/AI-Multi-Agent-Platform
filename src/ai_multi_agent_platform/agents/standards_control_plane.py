"""Control Plane integration for the optional standard Agent catalog (issue #77)."""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext, json_object
from ai_multi_agent_platform.domain import OwnerRef, Provenance

from .service import AgentService
from .standards import (
    STANDARD_AGENT_TEMPLATES,
    STANDARD_TEAM_TEMPLATES,
    STARTER_CATALOG_SOURCE,
    STARTER_CATALOG_VERSION,
    CapabilityInventory,
    StandardAgentTemplate,
    StandardTeamTemplate,
    bootstrap_standard_agents,
    get_standard_agent_template,
    get_standard_team_template,
)

STANDARD_AGENT_CATALOG_COLLECTION = "standard-agents"
STANDARD_TEAM_CATALOG_COLLECTION = "standard-agent-teams"
STANDARD_AGENT_CATALOG_REF = "standard-agent-catalog"
STANDARD_AGENT_CONTROL_PLANE_COMMANDS = (
    "standard-agent.bootstrap",
    "standard-agent.clone",
    "standard-agent-team.clone",
    "agent.delete",
    "agent-team.delete",
)

# These starters are useful only when their file-facing work is explicitly bound to a
# canonical project/workspace. The generic Agent contracts remain general-purpose; this
# starter-specific guard applies to the managed catalog clone workflow.
SCOPED_STANDARD_AGENT_KEYS = frozenset({"developer", "file_assistant"})
SCOPED_STANDARD_TEAM_KEYS = frozenset({"software_development"})


class StandardAgentCatalogResourceService:
    """Read-only discoverability for bundled standard Agent templates."""

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_agent_template_resource(template) for template in STANDARD_AGENT_TEMPLATES)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return _agent_template_resource(_standard_agent_template(resource_id))


class StandardAgentTeamCatalogResourceService:
    """Read-only discoverability for bundled standard Agent Team templates."""

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_team_template_resource(template) for template in STANDARD_TEAM_TEMPLATES)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return _team_template_resource(_standard_team_template(resource_id))


class StandardAgentCommandHandlers:
    """Lifecycle commands for the optional starter catalog and user-owned copies."""

    def __init__(
        self,
        service: AgentService,
        *,
        capability_inventory: CapabilityInventory | None = None,
    ) -> None:
        self.service = service
        self.capability_inventory = capability_inventory

    async def bootstrap(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context, payload
        if resource_ref != STANDARD_AGENT_CATALOG_REF:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"standard Agent bootstrap requires resource_ref={STANDARD_AGENT_CATALOG_REF!r}",
            )
        result = bootstrap_standard_agents(
            self.service,
            capability_inventory=self.capability_inventory,
        )
        return {
            "id": STANDARD_AGENT_CATALOG_REF,
            "type": "standard_agent_bootstrap_result",
            "catalog_version": STARTER_CATALOG_VERSION,
            "installed_agent_keys": list(result.installed_agent_keys),
            "preserved_agent_keys": list(result.preserved_agent_keys),
            "installed_team_keys": list(result.installed_team_keys),
            "preserved_team_keys": list(result.preserved_team_keys),
            "readiness": [json_object(item) for item in result.readiness],
        }

    async def clone_agent(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        template = _standard_agent_template(resource_ref)
        _validate_installed_agent_identity(self.service, template)
        owner_ref = _context_owner(context)
        project_id = _optional_string(payload, "project_id")
        workspace_id = _optional_string(payload, "workspace_id")
        if (
            template.key in SCOPED_STANDARD_AGENT_KEYS
            and project_id is None
            and workspace_id is None
        ):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "scoped standard Agent clone requires an explicit project_id or workspace_id",
                details={"agent_key": template.key},
            )
        revision = self.service.clone_agent(
            template.agent_id,
            revision=1,
            owner_ref=owner_ref,
            project_id=project_id,
            workspace_id=workspace_id,
            name=_optional_string(payload, "name"),
            provenance=_command_provenance(context, "standard-agent.clone", template.key),
        )
        return _agent_resource(self.service, revision.agent_id)

    async def clone_team(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        template = _standard_team_template(resource_ref)
        _validate_installed_team_identity(self.service, template)
        owner_ref = _context_owner(context)
        project_id = _optional_string(payload, "project_id")
        workspace_id = _optional_string(payload, "workspace_id")
        if (
            template.key in SCOPED_STANDARD_TEAM_KEYS
            and project_id is None
            and workspace_id is None
        ):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "scoped standard Agent Team clone requires an explicit project_id or workspace_id",
                details={"team_key": template.key},
            )
        revision = self.service.clone_team(
            template.team_id,
            revision=1,
            owner_ref=owner_ref,
            project_id=project_id,
            workspace_id=workspace_id,
            name=_optional_string(payload, "name"),
            provenance=_command_provenance(context, "standard-agent-team.clone", template.key),
        )
        return _team_resource(self.service, revision.team_id)

    async def delete_agent(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        self.service.delete_agent(resource_ref, expected_owner_ref=_context_owner(context))
        return {"id": resource_ref, "type": "agent", "deleted": True}

    async def delete_team(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        self.service.delete_team(resource_ref, expected_owner_ref=_context_owner(context))
        return {"id": resource_ref, "type": "agent_team", "deleted": True}


def register_standard_agent_control_plane(
    control_plane: ControlPlane,
    service: AgentService,
    *,
    capability_inventory: CapabilityInventory | None = None,
) -> None:
    """Expose catalog discovery/install and safe user-copy lifecycle through the Control Plane."""

    control_plane.register_resource_service(
        STANDARD_AGENT_CATALOG_COLLECTION,
        StandardAgentCatalogResourceService(),
    )
    control_plane.register_resource_service(
        STANDARD_TEAM_CATALOG_COLLECTION,
        StandardAgentTeamCatalogResourceService(),
    )
    handlers = StandardAgentCommandHandlers(
        service,
        capability_inventory=capability_inventory,
    )
    control_plane.register_command("standard-agent.bootstrap", handlers.bootstrap)
    control_plane.register_command("standard-agent.clone", handlers.clone_agent)
    control_plane.register_command("standard-agent-team.clone", handlers.clone_team)
    control_plane.register_command("agent.delete", handlers.delete_agent)
    control_plane.register_command("agent-team.delete", handlers.delete_team)


def _agent_template_resource(template: StandardAgentTemplate) -> dict[str, JsonValue]:
    return {
        "id": template.key,
        "type": "standard_agent_template",
        "definition_id": template.agent_id,
        "catalog_version": template.version,
        "catalog_source": STARTER_CATALOG_SOURCE,
        "permission_profile": template.permission_profile,
        "required_capability_ids": list(template.required_capability_ids),
        "optional_capability_ids": list(template.optional_capability_ids),
        "requires_explicit_scope": template.key in SCOPED_STANDARD_AGENT_KEYS,
        "profile": json_object(template.profile),
    }


def _team_template_resource(template: StandardTeamTemplate) -> dict[str, JsonValue]:
    members: list[JsonValue] = [
        {
            "agent_key": member.agent_key,
            "role": member.role,
            "required": member.required,
            "can_delegate_to_keys": list(member.can_delegate_to_keys),
        }
        for member in template.members
    ]
    return {
        "id": template.key,
        "type": "standard_agent_team_template",
        "definition_id": template.team_id,
        "catalog_version": template.version,
        "catalog_source": STARTER_CATALOG_SOURCE,
        "name": template.name,
        "description": template.description,
        "leader_agent_key": template.leader_agent_key,
        "members": members,
        "max_parallel_agents": template.max_parallel_agents,
        "max_steps": template.max_steps,
        "requires_explicit_scope": template.key in SCOPED_STANDARD_TEAM_KEYS,
    }


def _standard_agent_template(key: str) -> StandardAgentTemplate:
    try:
        return get_standard_agent_template(key)
    except KeyError as exc:
        raise ContractError(
            ErrorCode.NOT_FOUND,
            f"standard Agent template not found: {key}",
        ) from exc


def _standard_team_template(key: str) -> StandardTeamTemplate:
    try:
        return get_standard_team_template(key)
    except KeyError as exc:
        raise ContractError(
            ErrorCode.NOT_FOUND,
            f"standard Agent Team template not found: {key}",
        ) from exc


def _validate_installed_agent_identity(
    service: AgentService,
    template: StandardAgentTemplate,
) -> None:
    revision = service.repository.get_agent_revision(template.agent_id, 1)
    metadata = revision.profile.metadata
    if (
        metadata.get("starter_catalog_source") != STARTER_CATALOG_SOURCE
        or metadata.get("starter_key") != template.key
        or metadata.get("starter_kind") != "agent"
    ):
        raise ContractError(
            ErrorCode.CONFLICT,
            "stable standard Agent ID is not occupied by the expected catalog definition",
            details={"agent_key": template.key, "agent_id": template.agent_id},
        )


def _validate_installed_team_identity(
    service: AgentService,
    template: StandardTeamTemplate,
) -> None:
    revision = service.repository.get_team_revision(template.team_id, 1)
    metadata = revision.profile.metadata
    if (
        metadata.get("starter_catalog_source") != STARTER_CATALOG_SOURCE
        or metadata.get("starter_key") != template.key
        or metadata.get("starter_kind") != "team"
    ):
        raise ContractError(
            ErrorCode.CONFLICT,
            "stable standard Agent Team ID is not occupied by the expected catalog definition",
            details={"team_key": template.key, "team_id": template.team_id},
        )


def _context_owner(context: RequestContext) -> OwnerRef:
    owner_type = context.actor.owner_type
    owner_id = context.actor.owner_id
    if owner_type is None or owner_id is None:
        raise ContractError(
            ErrorCode.UNAUTHORIZED,
            "standard Agent lifecycle command requires authenticated owner context",
        )
    return OwnerRef(type=owner_type, id=owner_id)


def _optional_string(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _command_provenance(
    context: RequestContext,
    operation: str,
    starter_key: str,
) -> Provenance:
    return Provenance(
        source=STARTER_CATALOG_SOURCE,
        actor_ref=context.actor.principal_ref,
        details={
            "operation": operation,
            "starter_key": starter_key,
            "starter_catalog_version": STARTER_CATALOG_VERSION,
        },
    )


def _agent_resource(service: AgentService, agent_id: str) -> dict[str, JsonValue]:
    definition = service.repository.get_agent(agent_id)
    revision = service.repository.get_agent_revision(agent_id, definition.current_revision)
    return {
        "id": agent_id,
        "type": "agent",
        "current_revision": definition.current_revision,
        "project_id": definition.project_id,
        "workspace_id": definition.workspace_id,
        "owner_ref": json_object(definition.owner_ref),
        "revision": json_object(revision),
    }


def _team_resource(service: AgentService, team_id: str) -> dict[str, JsonValue]:
    definition = service.repository.get_team(team_id)
    revision = service.repository.get_team_revision(team_id, definition.current_revision)
    return {
        "id": team_id,
        "type": "agent_team",
        "current_revision": definition.current_revision,
        "project_id": definition.project_id,
        "workspace_id": definition.workspace_id,
        "owner_ref": json_object(definition.owner_ref),
        "revision": json_object(revision),
    }
