"""Concrete Template handlers for canonical Agents and Agent Teams."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from ai_multi_agent_platform.agents import (
    AgentProfile,
    AgentService,
    AgentTeamProfile,
    agent_profile_from_json,
    agent_team_profile_from_json,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import FrozenJsonValue, JsonValue
from ai_multi_agent_platform.control_plane.models import json_value
from ai_multi_agent_platform.domain import OwnerRef, Provenance

from .application import (
    ContextualTemplateHandlerRegistry,
    TemplateInstantiationContext,
)
from .models import (
    CapabilityRequirement,
    TemplateConfiguration,
    TemplateContent,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateRequirements,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateTrust,
    TemplateType,
)
from .service import TemplateService


@dataclass(slots=True)
class AgentTemplateHandler:
    """Instantiate one ordinary canonical Agent through AgentService."""

    service: AgentService
    template_type = TemplateType.AGENT

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        payload = _payload(revision)
        _agent_profile(payload)
        _optional_canonical_string(payload, "project_id")
        _optional_canonical_string(payload, "workspace_id")
        return (
            TemplateResourceChange(
                resource_type="agent",
                action="create",
                description=f"Create Agent from {revision.template_id}@{revision.revision}",
            ),
        )

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        payload = _payload(revision)
        created = self.service.create_agent(
            _agent_profile(payload),
            owner_ref=provenance.applied_by,
            project_id=_optional_canonical_string(payload, "project_id"),
            workspace_id=_optional_canonical_string(payload, "workspace_id"),
            provenance=_resource_provenance(provenance, context),
        )
        return (TemplateResourceRef(resource_type="agent", resource_id=created.agent_id),)


@dataclass(slots=True)
class AgentTeamTemplateHandler:
    """Instantiate a Team after remapping portable Agent-Template member references."""

    service: AgentService
    template_type = TemplateType.AGENT_TEAM

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        payload = _payload(revision)
        profile = _mapping(_required(payload, "profile"), "profile")
        referenced = _team_template_ids(profile)
        declared = {dependency.template_id for dependency in revision.content.dependencies}
        undeclared = sorted(referenced - declared)
        if undeclared:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Agent Team template references Agent templates that are not dependencies",
                details={"template_ids": cast(JsonValue, undeclared)},
            )
        _optional_canonical_string(payload, "project_id")
        _optional_canonical_string(payload, "workspace_id")
        return (
            TemplateResourceChange(
                resource_type="agent_team",
                action="create",
                description=f"Create Agent Team from {revision.template_id}@{revision.revision}",
            ),
        )

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        payload = _payload(revision)
        profile = _materialize_team_profile(
            self.service,
            _mapping(_required(payload, "profile"), "profile"),
            context,
        )
        created = self.service.create_team(
            profile,
            owner_ref=provenance.applied_by,
            project_id=_optional_canonical_string(payload, "project_id"),
            workspace_id=_optional_canonical_string(payload, "workspace_id"),
            provenance=_resource_provenance(provenance, context),
        )
        return (TemplateResourceRef(resource_type="agent_team", resource_id=created.team_id),)


@dataclass(slots=True)
class AgentTemplateExporter:
    """Create a portable draft Template from an existing canonical Agent revision."""

    agents: AgentService
    templates: TemplateService

    def create_from_agent(
        self,
        agent_id: str,
        *,
        owner_ref: OwnerRef,
        author: str,
        revision: int | None = None,
        name: str | None = None,
    ) -> TemplateRevision:
        source = self.agents.get_agent_revision(agent_id, revision)
        profile = portable_agent_profile_payload(source.profile)
        requirements = TemplateRequirements(
            capabilities=_capability_requirements(source.profile),
            model_policy_refs=(
                (source.profile.model.routing_profile_ref,)
                if source.profile.model.routing_profile_ref is not None
                else ()
            ),
        )
        content = TemplateContent(
            name=name or source.profile.name,
            description=f"Template exported from Agent {source.agent_id}@{source.revision}",
            template_type=TemplateType.AGENT,
            configuration=TemplateConfiguration(
                payload={
                    "profile": profile,
                    "project_id": None,
                    "workspace_id": None,
                }
            ),
            requirements=requirements,
            provenance=TemplateProvenance(
                author=author,
                source="canonical-agent-export",
                trust=TemplateTrust.LOCAL,
                metadata={
                    "source_resource_type": "agent",
                    "source_resource_id": source.agent_id,
                    "source_resource_revision": source.revision,
                    "source_project_id": source.project_id,
                    "source_workspace_id": source.workspace_id,
                    "source_default_project_id": source.profile.workspace_defaults.project_id,
                    "source_default_workspace_id": source.profile.workspace_defaults.workspace_id,
                },
            ),
            tags=("agent", "exported"),
        )
        return self.templates.create_draft(
            owner_ref=owner_ref,
            content=content,
        )


def register_agent_template_handlers(
    registry: ContextualTemplateHandlerRegistry,
    service: AgentService,
) -> None:
    """Register canonical Agent and Agent Team Template handlers."""

    registry.register(AgentTemplateHandler(service))
    registry.register(AgentTeamTemplateHandler(service))


def portable_agent_profile_payload(profile: AgentProfile) -> Mapping[str, FrozenJsonValue]:
    """Return a deployment-portable Agent profile or fail on undeclared local references."""

    unsupported: list[str] = []
    if profile.data_access.memory_config_refs:
        unsupported.append("data_access.memory_config_refs")
    if profile.data_access.knowledge_source_ids:
        unsupported.append("data_access.knowledge_source_ids")
    if profile.policy_hooks.authorization_profile_ref is not None:
        unsupported.append("policy_hooks.authorization_profile_ref")
    if profile.policy_hooks.verification_policy_refs:
        unsupported.append("policy_hooks.verification_policy_refs")
    if unsupported:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "Agent Template export cannot preserve deployment-local Agent references "
            "without declared portable dependencies",
            details={"fields": cast(JsonValue, unsupported)},
        )

    payload = dict(_freeze_json_object(json_value(profile), "Agent profile"))
    payload["workspace_defaults"] = {
        "project_id": None,
        "workspace_id": None,
    }
    return payload


def _agent_profile(payload: Mapping[str, object]) -> AgentProfile:
    try:
        return agent_profile_from_json(_required(payload, "profile"))
    except (TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"invalid Agent template profile: {exc}",
        ) from exc


def _materialize_team_profile(
    service: AgentService,
    portable: Mapping[str, object],
    context: TemplateInstantiationContext,
) -> AgentTeamProfile:
    members_raw = portable.get("members")
    if not isinstance(members_raw, list | tuple) or not members_raw:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Agent Team template profile.members must be a non-empty array",
        )

    resolved_ids: dict[str, str] = {}
    member_specs: list[Mapping[str, object]] = []
    for raw in members_raw:
        member = _mapping(raw, "team member")
        template_id = _required_string(member, "agent_template_id")
        if template_id in resolved_ids:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Agent Team template cannot use the same Agent template more than once",
                details={"template_id": template_id},
            )
        template_revision = _optional_positive_int(member, "agent_template_revision")
        resource = context.single_resource_for(
            template_id,
            revision=template_revision,
            resource_type="agent",
        )
        resolved_ids[template_id] = resource.resource_id
        member_specs.append(member)

    members: list[dict[str, object]] = []
    for member in member_specs:
        template_id = _required_string(member, "agent_template_id")
        agent_id = resolved_ids[template_id]
        agent_revision = service.get_agent_revision(agent_id).revision
        delegate_templates = _string_tuple(member, "can_delegate_to_template_ids")
        unknown = sorted(set(delegate_templates) - set(resolved_ids))
        if unknown:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Agent Team delegation references an unknown Agent template member",
                details={"template_ids": cast(JsonValue, unknown)},
            )
        members.append(
            {
                "agent": {"agent_id": agent_id, "revision": agent_revision},
                "role": _required_string(member, "role"),
                "required": _optional_bool(member, "required", True),
                "can_delegate_to": [resolved_ids[item] for item in delegate_templates],
            }
        )

    leader_template_id = _optional_string(portable, "leader_agent_template_id")
    leader_agent_id: str | None = None
    if leader_template_id is not None:
        try:
            leader_agent_id = resolved_ids[leader_template_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Agent Team leader must reference one configured Agent template member",
                details={"template_id": leader_template_id},
            ) from exc

    canonical: dict[str, object] = {
        "name": _required_string(portable, "name"),
        "members": members,
        "description": _optional_string(portable, "description") or "",
        "coordination_policy_ref": _optional_string(portable, "coordination_policy_ref"),
        "leader_agent_id": leader_agent_id,
        "shared_capability_ids": list(_string_tuple(portable, "shared_capability_ids")),
        "shared_resource_refs": list(_string_tuple(portable, "shared_resource_refs")),
        "max_parallel_agents": _optional_positive_int(portable, "max_parallel_agents"),
        "max_steps": _optional_positive_int(portable, "max_steps"),
        "unavailable_member_policy": _optional_string(portable, "unavailable_member_policy")
        or "fail",
        "enabled": _optional_bool(portable, "enabled", True),
        "metadata": dict(_mapping(portable.get("metadata", {}), "team metadata")),
    }
    try:
        return agent_team_profile_from_json(canonical)
    except (TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"invalid Agent Team template profile: {exc}",
        ) from exc


def _team_template_ids(profile: Mapping[str, object]) -> set[str]:
    members_raw = profile.get("members")
    if not isinstance(members_raw, list | tuple) or not members_raw:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "Agent Team template profile.members must be a non-empty array",
        )
    result: set[str] = set()
    for raw in members_raw:
        member = _mapping(raw, "team member")
        result.add(_required_string(member, "agent_template_id"))
        result.update(_string_tuple(member, "can_delegate_to_template_ids"))
    leader = _optional_string(profile, "leader_agent_template_id")
    if leader is not None:
        result.add(leader)
    return result


def _resource_provenance(
    provenance: TemplateInstantiationProvenance,
    context: TemplateInstantiationContext,
) -> Provenance:
    owner = provenance.applied_by
    return Provenance(
        source="template",
        actor_ref=f"{owner.type}:{owner.id}",
        details={
            "template_id": provenance.source.template_id,
            "template_revision": provenance.source.revision,
            "template_instance_id": context.instance_id,
        },
    )


def _capability_requirements(profile: AgentProfile) -> tuple[CapabilityRequirement, ...]:
    result: list[CapabilityRequirement] = []
    for constraint in profile.capabilities.constraints:
        version_constraint: str | None = None
        if constraint.exact_version is not None:
            version_constraint = f"=={constraint.exact_version}"
        elif constraint.minimum_version is not None or constraint.maximum_version is not None:
            bounds: list[str] = []
            if constraint.minimum_version is not None:
                bounds.append(f">={constraint.minimum_version}")
            if constraint.maximum_version is not None:
                bounds.append(f"<={constraint.maximum_version}")
            version_constraint = ",".join(bounds)
        result.append(
            CapabilityRequirement(
                capability_id=constraint.capability_id,
                optional=not constraint.required,
                version_constraint=version_constraint,
                privileged=constraint.approval_ref is not None,
            )
        )
    return tuple(result)


def _payload(revision: TemplateRevision) -> Mapping[str, object]:
    payload = revision.content.configuration.payload
    if payload is None:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "canonical Agent/Team handlers require an inline Template payload",
        )
    return cast(Mapping[str, object], payload)


def _freeze_json_object(value: JsonValue, name: str) -> Mapping[str, FrozenJsonValue]:
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"{name} did not serialize as an object")
    return {key: _freeze_json(item) for key, item in value.items()}


def _freeze_json(value: JsonValue) -> FrozenJsonValue:
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, dict):
        return {key: _freeze_json(item) for key, item in value.items()}
    return value


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _required(mapping: Mapping[str, object], name: str) -> object:
    if name not in mapping:
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"missing required field: {name}")
    return mapping[name]


def _required_string(mapping: Mapping[str, object], name: str) -> str:
    value = mapping.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{name} must be a non-blank string")
    return value


def _optional_string(mapping: Mapping[str, object], name: str) -> str | None:
    value = mapping.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"{name} must be a non-blank string or null",
        )
    return value


def _optional_canonical_string(mapping: Mapping[str, object], name: str) -> str | None:
    return _optional_string(mapping, name)


def _optional_positive_int(mapping: Mapping[str, object], name: str) -> int | None:
    value = mapping.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"{name} must be an integer >= 1 or null",
        )
    return value


def _optional_bool(mapping: Mapping[str, object], name: str, default: bool) -> bool:
    value = mapping.get(name, default)
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{name} must be boolean")
    return value


def _string_tuple(mapping: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = mapping.get(name, ())
    if not isinstance(value, list | tuple) or not all(isinstance(item, str) for item in value):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"{name} must be an array of strings",
        )
    result = tuple(cast(str, item) for item in value)
    if any(not item.strip() for item in result):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"{name} must not contain blank strings",
        )
    return result
