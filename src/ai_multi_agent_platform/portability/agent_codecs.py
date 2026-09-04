"""Portable codecs for canonical Agent and Agent Team revision histories."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Literal, cast

from ai_multi_agent_platform.agents.models import (
    AgentCapabilityPolicy,
    AgentDataAccess,
    AgentDefinition,
    AgentInstructions,
    AgentModelPolicy,
    AgentPolicyHooks,
    AgentProfile,
    AgentRevision,
    AgentRevisionRef,
    AgentTeamDefinition,
    AgentTeamMember,
    AgentTeamProfile,
    AgentTeamRevision,
    AgentWorkspaceDefaults,
    CapabilityConstraint,
    InstructionSource,
    ModelFallbackPolicy,
    UnavailableMemberPolicy,
)
from ai_multi_agent_platform.agents.repository import AgentRepository
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data.models import MemoryScope
from ai_multi_agent_platform.domain import OwnerRef, Provenance
from ai_multi_agent_platform.models.types import RoutingRequirements

from .dependencies import resource_dependency
from .models import DependencyKind, DependencyRequirement, IdPolicy, PortableResource
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry

AGENT_PORTABLE_SCHEMA_VERSION = "1"
AGENT_RESOURCE_TYPE = "agent"
AGENT_TEAM_RESOURCE_TYPE = "agent_team"


@dataclass(frozen=True, slots=True)
class AgentPortableSnapshot:
    definition: AgentDefinition
    revisions: tuple[AgentRevision, ...]

    def __post_init__(self) -> None:
        if not self.revisions:
            raise ValueError("portable Agent snapshot requires revision history")
        if any(item.agent_id != self.definition.agent_id for item in self.revisions):
            raise ValueError("portable Agent revisions must match the Agent definition")
        numbers = tuple(item.revision for item in self.revisions)
        if numbers != tuple(range(1, self.definition.current_revision + 1)):
            raise ValueError("portable Agent revision history must be contiguous from revision 1")
        if self.revisions[-1].revision != self.definition.current_revision:
            raise ValueError("portable Agent definition must point at the latest exported revision")


@dataclass(frozen=True, slots=True)
class AgentTeamPortableSnapshot:
    definition: AgentTeamDefinition
    revisions: tuple[AgentTeamRevision, ...]

    def __post_init__(self) -> None:
        if not self.revisions:
            raise ValueError("portable Agent Team snapshot requires revision history")
        if any(item.team_id != self.definition.team_id for item in self.revisions):
            raise ValueError("portable Team revisions must match the Team definition")
        numbers = tuple(item.revision for item in self.revisions)
        if numbers != tuple(range(1, self.definition.current_revision + 1)):
            raise ValueError("portable Team revision history must be contiguous from revision 1")
        if self.revisions[-1].revision != self.definition.current_revision:
            raise ValueError("portable Team definition must point at the latest exported revision")


def snapshot_agent(repository: AgentRepository, agent_id: str) -> AgentPortableSnapshot:
    return AgentPortableSnapshot(
        definition=repository.get_agent(agent_id),
        revisions=repository.list_agent_revisions(agent_id),
    )


def snapshot_agent_team(repository: AgentRepository, team_id: str) -> AgentTeamPortableSnapshot:
    return AgentTeamPortableSnapshot(
        definition=repository.get_team(team_id),
        revisions=repository.list_team_revisions(team_id),
    )


class AgentPortableCodec:
    resource_type = AGENT_RESOURCE_TYPE

    def __init__(self, *, id_policy: IdPolicy = IdPolicy.PRESERVE) -> None:
        self.id_policy = id_policy

    def serialize(self, value: object) -> ResourceExport:
        if not isinstance(value, AgentPortableSnapshot):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Agent portable codec requires an AgentPortableSnapshot",
            )
        dependencies = _agent_dependencies(value)
        return ResourceExport(
            resource_id=value.definition.agent_id,
            resource_version=str(value.definition.current_revision),
            payload={
                "schema_version": AGENT_PORTABLE_SCHEMA_VERSION,
                "definition": _encode(value.definition),
                "revisions": _encode(value.revisions),
            },
            id_policy=self.id_policy,
            dependencies=dependencies,
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"Agent codec cannot deserialize resource type {resource.resource_type!r}",
            )
        try:
            snapshot = _agent_snapshot_from_payload(resource.payload)
            return _remap_agent_snapshot(snapshot, context)
        except ContractError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable Agent payload",
                details={"resource_id": resource.resource_id},
            ) from exc


class AgentTeamPortableCodec:
    resource_type = AGENT_TEAM_RESOURCE_TYPE

    def __init__(self, *, id_policy: IdPolicy = IdPolicy.PRESERVE) -> None:
        self.id_policy = id_policy

    def serialize(self, value: object) -> ResourceExport:
        if not isinstance(value, AgentTeamPortableSnapshot):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Agent Team portable codec requires an AgentTeamPortableSnapshot",
            )
        return ResourceExport(
            resource_id=value.definition.team_id,
            resource_version=str(value.definition.current_revision),
            payload={
                "schema_version": AGENT_PORTABLE_SCHEMA_VERSION,
                "definition": _encode(value.definition),
                "revisions": _encode(value.revisions),
            },
            id_policy=self.id_policy,
            dependencies=_team_dependencies(value),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"Agent Team codec cannot deserialize resource type {resource.resource_type!r}",
            )
        try:
            snapshot = _team_snapshot_from_payload(resource.payload)
            return _remap_team_snapshot(snapshot, context)
        except ContractError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable Agent Team payload",
                details={"resource_id": resource.resource_id},
            ) from exc


def register_agent_portability_codecs(
    registry: ResourceSerializerRegistry,
    *,
    agent_id_policy: IdPolicy = IdPolicy.PRESERVE,
    team_id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> None:
    registry.register(AgentPortableCodec(id_policy=agent_id_policy))
    registry.register(AgentTeamPortableCodec(id_policy=team_id_policy))


def _agent_dependencies(snapshot: AgentPortableSnapshot) -> tuple[DependencyRequirement, ...]:
    dependencies: set[DependencyRequirement] = set()
    for revision in snapshot.revisions:
        profile = revision.profile
        explicit_model = profile.model.requirements.explicit_model_id
        if explicit_model is not None:
            dependencies.add(
                DependencyRequirement(
                    kind=DependencyKind.MODEL,
                    identifier=explicit_model,
                    purpose="Agent model requirement",
                )
            )
        for constraint in profile.capabilities.constraints:
            dependencies.add(
                DependencyRequirement(
                    kind=DependencyKind.CAPABILITY,
                    identifier=constraint.capability_id,
                    required=constraint.required,
                    version_constraint=_capability_version_constraint(constraint),
                    purpose="Agent capability constraint",
                )
            )
        for source_id in profile.data_access.knowledge_source_ids:
            dependencies.add(
                resource_dependency(
                    "knowledge_source",
                    source_id,
                    purpose="Agent knowledge-source access",
                )
            )
        for config_ref in profile.data_access.memory_config_refs:
            dependencies.add(
                resource_dependency(
                    "memory_config",
                    config_ref,
                    purpose="Agent memory configuration",
                )
            )
        _add_scope_dependencies(dependencies, revision.project_id, revision.workspace_id, "Agent")
    return _sorted_dependencies(dependencies)


def _team_dependencies(snapshot: AgentTeamPortableSnapshot) -> tuple[DependencyRequirement, ...]:
    dependencies: set[DependencyRequirement] = set()
    for revision in snapshot.revisions:
        profile = revision.profile
        for member in profile.members:
            dependencies.add(
                resource_dependency(
                    AGENT_RESOURCE_TYPE,
                    member.agent.agent_id,
                    required=member.required,
                    version_constraint=f">={member.agent.revision}",
                    purpose=f"Agent Team member revision {member.agent.revision}",
                )
            )
        for capability_id in profile.shared_capability_ids:
            dependencies.add(
                DependencyRequirement(
                    kind=DependencyKind.CAPABILITY,
                    identifier=capability_id,
                    purpose="Agent Team shared capability",
                )
            )
        _add_scope_dependencies(dependencies, revision.project_id, revision.workspace_id, "Team")
    return _sorted_dependencies(dependencies)


def _add_scope_dependencies(
    dependencies: set[DependencyRequirement],
    project_id: str | None,
    workspace_id: str | None,
    purpose_prefix: str,
) -> None:
    if project_id is not None:
        dependencies.add(
            resource_dependency("project", project_id, purpose=f"{purpose_prefix} project scope")
        )
    if workspace_id is not None:
        dependencies.add(
            resource_dependency(
                "workspace", workspace_id, purpose=f"{purpose_prefix} workspace scope"
            )
        )


def _capability_version_constraint(constraint: CapabilityConstraint) -> str | None:
    if constraint.exact_version is not None:
        return f"=={constraint.exact_version}"
    parts: list[str] = []
    if constraint.minimum_version is not None:
        parts.append(f">={constraint.minimum_version}")
    if constraint.maximum_version is not None:
        parts.append(f"<={constraint.maximum_version}")
    return ",".join(parts) or None


def _sorted_dependencies(
    dependencies: set[DependencyRequirement],
) -> tuple[DependencyRequirement, ...]:
    return tuple(
        sorted(
            dependencies,
            key=lambda item: (
                item.kind.value,
                item.identifier,
                item.required,
                item.version_constraint or "",
                item.purpose or "",
            ),
        )
    )


def _encode(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return _encode(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _encode(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        encoded: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("portable Agent mappings require string keys")
            encoded[key] = _encode(item)
        return encoded
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_encode(item) for item in value]
    raise TypeError(f"unsupported portable Agent value: {type(value).__name__}")


def _agent_snapshot_from_payload(payload: dict[str, JsonValue]) -> AgentPortableSnapshot:
    _require_schema(payload)
    definition = _agent_definition(payload.get("definition"))
    revisions = tuple(
        _agent_revision(item) for item in _array(payload.get("revisions"), "revisions")
    )
    return AgentPortableSnapshot(definition=definition, revisions=revisions)


def _team_snapshot_from_payload(payload: dict[str, JsonValue]) -> AgentTeamPortableSnapshot:
    _require_schema(payload)
    definition = _team_definition(payload.get("definition"))
    revisions = tuple(
        _team_revision(item) for item in _array(payload.get("revisions"), "revisions")
    )
    return AgentTeamPortableSnapshot(definition=definition, revisions=revisions)


def _require_schema(payload: dict[str, JsonValue]) -> None:
    if payload.get("schema_version") != AGENT_PORTABLE_SCHEMA_VERSION:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "unsupported portable Agent/Team schema version",
            details={"supported_schema_version": AGENT_PORTABLE_SCHEMA_VERSION},
        )


def _agent_definition(value: JsonValue | None) -> AgentDefinition:
    data = _object(value, "Agent definition")
    return AgentDefinition(
        agent_id=_string(data, "agent_id"),
        owner_ref=_owner(data.get("owner_ref")),
        current_revision=_integer(data, "current_revision"),
        project_id=_optional_string(data, "project_id"),
        workspace_id=_optional_string(data, "workspace_id"),
        created_at=_timestamp(data.get("created_at"), "created_at"),
        updated_at=_timestamp(data.get("updated_at"), "updated_at"),
    )


def _agent_revision(value: JsonValue) -> AgentRevision:
    data = _object(value, "Agent revision")
    return AgentRevision(
        agent_id=_string(data, "agent_id"),
        revision=_integer(data, "revision"),
        profile=_agent_profile(data.get("profile")),
        owner_ref=_owner(data.get("owner_ref")),
        project_id=_optional_string(data, "project_id"),
        workspace_id=_optional_string(data, "workspace_id"),
        created_at=_timestamp(data.get("created_at"), "created_at"),
        provenance=_provenance(data.get("provenance")),
    )


def _agent_profile(value: JsonValue | None) -> AgentProfile:
    data = _object(value, "Agent profile")
    instructions = _object(data.get("instructions"), "Agent instructions")
    model = _object(data.get("model"), "Agent model policy")
    capabilities = _object(data.get("capabilities"), "Agent capability policy")
    data_access = _object(data.get("data_access"), "Agent data access")
    workspace = _object(data.get("workspace_defaults"), "Agent workspace defaults")
    hooks = _object(data.get("policy_hooks"), "Agent policy hooks")
    requirements = _object(model.get("requirements"), "routing requirements")
    return AgentProfile(
        name=_string(data, "name"),
        role=_string(data, "role"),
        instructions=AgentInstructions(
            role=_instruction_source(instructions.get("role")),
            platform_constraint_refs=_strings(
                instructions.get("platform_constraint_refs"), "platform_constraint_refs"
            ),
            project_instruction_refs=_strings(
                instructions.get("project_instruction_refs"), "project_instruction_refs"
            ),
        ),
        description=_string_allow_empty(data, "description"),
        model=AgentModelPolicy(
            requirements=RoutingRequirements(
                explicit_model_id=_optional_string(requirements, "explicit_model_id"),
                min_context_window=_optional_integer(requirements, "min_context_window"),
                tool_calling=_boolean(requirements, "tool_calling"),
                structured_output=_boolean(requirements, "structured_output"),
                streaming=_boolean(requirements, "streaming"),
                modalities=_strings(requirements.get("modalities"), "modalities"),
                reasoning=_strings(requirements.get("reasoning"), "reasoning"),
                local_only=_boolean(requirements, "local_only"),
                self_hosted_only=_boolean(requirements, "self_hosted_only"),
            ),
            routing_profile_ref=_optional_string(model, "routing_profile_ref"),
            allow_task_override=_boolean(model, "allow_task_override"),
            fallback=ModelFallbackPolicy(_string(model, "fallback")),
        ),
        capabilities=AgentCapabilityPolicy(
            allowed=_strings(capabilities.get("allowed"), "allowed"),
            denied=_strings(capabilities.get("denied"), "denied"),
            constraints=tuple(
                _capability_constraint(item)
                for item in _array(capabilities.get("constraints"), "constraints")
            ),
        ),
        data_access=AgentDataAccess(
            memory_scopes=tuple(
                MemoryScope(item)
                for item in _strings(data_access.get("memory_scopes"), "memory_scopes")
            ),
            memory_config_refs=_strings(
                data_access.get("memory_config_refs"), "memory_config_refs"
            ),
            knowledge_source_ids=_strings(
                data_access.get("knowledge_source_ids"), "knowledge_source_ids"
            ),
            allow_user_memory=_boolean(data_access, "allow_user_memory"),
        ),
        workspace_defaults=AgentWorkspaceDefaults(
            project_id=_optional_string(workspace, "project_id"),
            workspace_id=_optional_string(workspace, "workspace_id"),
        ),
        policy_hooks=AgentPolicyHooks(
            authorization_profile_ref=_optional_string(hooks, "authorization_profile_ref"),
            verification_policy_refs=_strings(
                hooks.get("verification_policy_refs"), "verification_policy_refs"
            ),
        ),
        resource_hints=_object(data.get("resource_hints"), "resource_hints"),
        enabled=_boolean(data, "enabled"),
        metadata=_object(data.get("metadata"), "metadata"),
    )


def _instruction_source(value: JsonValue | None) -> InstructionSource:
    data = _object(value, "instruction source")
    return InstructionSource(
        content=_optional_string(data, "content"),
        ref=_optional_string(data, "ref"),
        version=_optional_string(data, "version"),
    )


def _capability_constraint(value: JsonValue) -> CapabilityConstraint:
    data = _object(value, "capability constraint")
    return CapabilityConstraint(
        capability_id=_string(data, "capability_id"),
        required=_boolean(data, "required"),
        exact_version=_optional_string(data, "exact_version"),
        minimum_version=_optional_string(data, "minimum_version"),
        maximum_version=_optional_string(data, "maximum_version"),
        required_features=_strings(data.get("required_features"), "required_features"),
        approval_ref=_optional_string(data, "approval_ref"),
    )


def _team_definition(value: JsonValue | None) -> AgentTeamDefinition:
    data = _object(value, "Agent Team definition")
    return AgentTeamDefinition(
        team_id=_string(data, "team_id"),
        owner_ref=_owner(data.get("owner_ref")),
        current_revision=_integer(data, "current_revision"),
        project_id=_optional_string(data, "project_id"),
        workspace_id=_optional_string(data, "workspace_id"),
        created_at=_timestamp(data.get("created_at"), "created_at"),
        updated_at=_timestamp(data.get("updated_at"), "updated_at"),
    )


def _team_revision(value: JsonValue) -> AgentTeamRevision:
    data = _object(value, "Agent Team revision")
    return AgentTeamRevision(
        team_id=_string(data, "team_id"),
        revision=_integer(data, "revision"),
        profile=_team_profile(data.get("profile")),
        owner_ref=_owner(data.get("owner_ref")),
        project_id=_optional_string(data, "project_id"),
        workspace_id=_optional_string(data, "workspace_id"),
        created_at=_timestamp(data.get("created_at"), "created_at"),
        provenance=_provenance(data.get("provenance")),
    )


def _team_profile(value: JsonValue | None) -> AgentTeamProfile:
    data = _object(value, "Agent Team profile")
    return AgentTeamProfile(
        name=_string(data, "name"),
        members=tuple(_team_member(item) for item in _array(data.get("members"), "members")),
        description=_string_allow_empty(data, "description"),
        coordination_policy_ref=_optional_string(data, "coordination_policy_ref"),
        leader_agent_id=_optional_string(data, "leader_agent_id"),
        shared_capability_ids=_strings(data.get("shared_capability_ids"), "shared_capability_ids"),
        shared_resource_refs=_strings(data.get("shared_resource_refs"), "shared_resource_refs"),
        max_parallel_agents=_optional_integer(data, "max_parallel_agents"),
        max_steps=_optional_integer(data, "max_steps"),
        unavailable_member_policy=UnavailableMemberPolicy(
            _string(data, "unavailable_member_policy")
        ),
        enabled=_boolean(data, "enabled"),
        metadata=_object(data.get("metadata"), "metadata"),
    )


def _team_member(value: JsonValue) -> AgentTeamMember:
    data = _object(value, "Agent Team member")
    agent = _object(data.get("agent"), "Agent revision reference")
    return AgentTeamMember(
        agent=AgentRevisionRef(
            agent_id=_string(agent, "agent_id"),
            revision=_integer(agent, "revision"),
        ),
        role=_string(data, "role"),
        required=_boolean(data, "required"),
        can_delegate_to=_strings(data.get("can_delegate_to"), "can_delegate_to"),
    )


def _remap_agent_snapshot(
    snapshot: AgentPortableSnapshot,
    context: ImportContext,
) -> AgentPortableSnapshot:
    source_id = snapshot.definition.agent_id
    target_id = context.remap(AGENT_RESOURCE_TYPE, source_id)
    definition = replace(
        snapshot.definition,
        agent_id=target_id,
        project_id=_remap_optional(context, "project", snapshot.definition.project_id),
        workspace_id=_remap_optional(context, "workspace", snapshot.definition.workspace_id),
    )
    revisions = tuple(
        replace(
            revision,
            agent_id=target_id,
            profile=_remap_agent_profile(revision.profile, context),
            project_id=_remap_optional(context, "project", revision.project_id),
            workspace_id=_remap_optional(context, "workspace", revision.workspace_id),
        )
        for revision in snapshot.revisions
    )
    return AgentPortableSnapshot(definition=definition, revisions=revisions)


def _remap_agent_profile(profile: AgentProfile, context: ImportContext) -> AgentProfile:
    requirements = profile.model.requirements
    explicit_model_id = requirements.explicit_model_id
    if explicit_model_id is not None:
        requirements = replace(
            requirements,
            explicit_model_id=context.remap("model", explicit_model_id),
        )
    return replace(
        profile,
        model=replace(profile.model, requirements=requirements),
        data_access=replace(
            profile.data_access,
            knowledge_source_ids=tuple(
                context.remap("knowledge_source", item)
                for item in profile.data_access.knowledge_source_ids
            ),
            memory_config_refs=tuple(
                context.remap("memory_config", item)
                for item in profile.data_access.memory_config_refs
            ),
        ),
        workspace_defaults=replace(
            profile.workspace_defaults,
            project_id=_remap_optional(context, "project", profile.workspace_defaults.project_id),
            workspace_id=_remap_optional(
                context, "workspace", profile.workspace_defaults.workspace_id
            ),
        ),
    )


def _remap_team_snapshot(
    snapshot: AgentTeamPortableSnapshot,
    context: ImportContext,
) -> AgentTeamPortableSnapshot:
    target_id = context.remap(AGENT_TEAM_RESOURCE_TYPE, snapshot.definition.team_id)
    definition = replace(
        snapshot.definition,
        team_id=target_id,
        project_id=_remap_optional(context, "project", snapshot.definition.project_id),
        workspace_id=_remap_optional(context, "workspace", snapshot.definition.workspace_id),
    )
    revisions = tuple(
        replace(
            revision,
            team_id=target_id,
            profile=_remap_team_profile(revision.profile, context),
            project_id=_remap_optional(context, "project", revision.project_id),
            workspace_id=_remap_optional(context, "workspace", revision.workspace_id),
        )
        for revision in snapshot.revisions
    )
    return AgentTeamPortableSnapshot(definition=definition, revisions=revisions)


def _remap_team_profile(profile: AgentTeamProfile, context: ImportContext) -> AgentTeamProfile:
    members = tuple(
        replace(
            member,
            agent=replace(
                member.agent,
                agent_id=context.remap(AGENT_RESOURCE_TYPE, member.agent.agent_id),
            ),
            can_delegate_to=tuple(
                context.remap(AGENT_RESOURCE_TYPE, item) for item in member.can_delegate_to
            ),
        )
        for member in profile.members
    )
    leader = profile.leader_agent_id
    return replace(
        profile,
        members=members,
        leader_agent_id=(None if leader is None else context.remap(AGENT_RESOURCE_TYPE, leader)),
    )


def _remap_optional(context: ImportContext, resource_type: str, value: str | None) -> str | None:
    return None if value is None else context.remap(resource_type, value)


def _owner(value: JsonValue | None) -> OwnerRef:
    data = _object(value, "owner_ref")
    owner_type = _string(data, "type")
    if owner_type not in {"user", "organization", "team", "service"}:
        raise ValueError("owner_ref.type is invalid")
    return OwnerRef(
        type=cast(Literal["user", "organization", "team", "service"], owner_type),
        id=_string(data, "id"),
    )


def _provenance(value: JsonValue | None) -> Provenance | None:
    if value is None:
        return None
    data = _object(value, "provenance")
    return Provenance(
        source=_string(data, "source"),
        actor_ref=_optional_string(data, "actor_ref"),
        details=_object(data.get("details"), "provenance.details"),
    )


def _object(value: object, field_name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    if not all(isinstance(key, str) and _is_json_value(item) for key, item in value.items()):
        raise ValueError(f"{field_name} contains non-JSON values")
    return cast(dict[str, JsonValue], value)


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _array(value: JsonValue | None, field_name: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a JSON array")
    return value


def _string(data: dict[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _string_allow_empty(data: dict[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_string(data: dict[str, JsonValue], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string or null")
    return value


def _integer(data: dict[str, JsonValue], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_integer(data: dict[str, JsonValue], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer or null")
    return value


def _boolean(data: dict[str, JsonValue], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be boolean")
    return value


def _strings(value: JsonValue | None, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a list of strings")
    return tuple(cast(str, item) for item in value)


def _timestamp(value: JsonValue | None, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed
