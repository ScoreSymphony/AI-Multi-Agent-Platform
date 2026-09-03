"""Control Plane extension for canonical Agent resources and commands (issue #33)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext, json_object
from ai_multi_agent_platform.data import MemoryScope
from ai_multi_agent_platform.domain import OwnerRef, Provenance
from ai_multi_agent_platform.models import RoutingRequirements

from .models import (
    AgentCapabilityPolicy,
    AgentDataAccess,
    AgentInstructions,
    AgentModelPolicy,
    AgentPolicyHooks,
    AgentProfile,
    AgentRevisionRef,
    AgentTeamMember,
    AgentTeamProfile,
    AgentWorkspaceDefaults,
    CapabilityConstraint,
    InstructionSource,
    ModelFallbackPolicy,
    UnavailableMemberPolicy,
)
from .runtime import (
    AgentOrchestratorMapper,
    AgentRuntime,
    ReferenceOrchestratorMapper,
)
from .service import AgentService

AGENT_COLLECTION = "agents"
AGENT_TEAM_COLLECTION = "agent-teams"
AGENT_RUN_COLLECTION = "agent-runs"

AGENT_COMMANDS = (
    "agent.create",
    "agent.update",
    "agent.clone",
    "agent.rollback",
    "agent.start",
    "agent-team.create",
    "agent-team.update",
    "agent-team.clone",
    "agent-team.rollback",
    "agent-team.start",
)

_SERVER_RESOLVED_EXECUTION_FIELDS = frozenset(
    {
        "available_capability_ids",
        "granted_permissions",
        "available_worker_capabilities",
    }
)


@dataclass(frozen=True, slots=True)
class AgentExecutionEnvironment:
    """Trusted runtime availability/authorization derived by the server composition."""

    available_capability_ids: frozenset[str] = frozenset()
    granted_permissions: frozenset[str] = frozenset()
    available_worker_capabilities: frozenset[str] = frozenset()


class AgentExecutionEnvironmentResolver(Protocol):
    """Resolve trusted execution grants from authenticated Control Plane context."""

    async def resolve(
        self,
        context: RequestContext,
        *,
        resource_ref: str,
        task_id: str,
        run_id: str,
        is_team: bool,
    ) -> AgentExecutionEnvironment: ...


class AgentResourceService:
    def __init__(self, service: AgentService) -> None:
        self.service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            _agent_resource(self.service, item.agent_id)
            for item in self.service.repository.list_agents()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return _agent_resource(self.service, resource_id)


class AgentTeamResourceService:
    def __init__(self, service: AgentService) -> None:
        self.service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            _team_resource(self.service, item.team_id)
            for item in self.service.repository.list_teams()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return _team_resource(self.service, resource_id)


class AgentRunResourceService:
    def __init__(self, service: AgentService) -> None:
        self.service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            _agent_run_resource(item) for item in self.service.repository.list_agent_runs()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return _agent_run_resource(self.service.repository.get_agent_run(resource_id))


class AgentCommandHandlers:
    """Mutation handlers bound to the generic Control Plane command seam."""

    def __init__(
        self,
        service: AgentService,
        runtime: AgentRuntime | None = None,
        *,
        orchestrator_mappers: Mapping[str, AgentOrchestratorMapper] | None = None,
        execution_environment_resolver: AgentExecutionEnvironmentResolver | None = None,
    ) -> None:
        self.service = service
        self.runtime = runtime
        self.execution_environment_resolver = execution_environment_resolver
        self.orchestrator_mappers = dict(orchestrator_mappers or {})
        for adapter_id, mapper in self.orchestrator_mappers.items():
            if not adapter_id.strip():
                raise ValueError("orchestrator mapper registry key must not be blank")
            if adapter_id != mapper.adapter_id:
                raise ValueError("orchestrator mapper registry key must match mapper.adapter_id")
            if adapter_id == ReferenceOrchestratorMapper.adapter_id:
                raise ValueError("reference-orchestrator is reserved by the Agent runtime")

    async def create_agent(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, AGENT_COLLECTION)
        revision = self.service.create_agent(
            _profile_from_json(_required(payload, "profile")),
            owner_ref=_owner_ref(payload.get("owner_ref"), context),
            project_id=_optional_string(payload, "project_id"),
            workspace_id=_optional_string(payload, "workspace_id"),
            provenance=_control_plane_provenance(context, "agent.create"),
        )
        return _agent_resource(self.service, revision.agent_id)

    async def update_agent(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current = self.service.repository.get_agent(resource_ref)
        project_id = (
            current.project_id
            if "project_id" not in payload
            else _optional_string(payload, "project_id")
        )
        workspace_id = (
            current.workspace_id
            if "workspace_id" not in payload
            else _optional_string(payload, "workspace_id")
        )
        revision = self.service.update_agent(
            resource_ref,
            _profile_from_json(_required(payload, "profile")),
            expected_revision=_required_positive_int(payload, "expected_revision"),
            owner_ref=_provided_owner_ref(payload.get("owner_ref")),
            project_id=project_id,
            workspace_id=workspace_id,
            provenance=_control_plane_provenance(context, "agent.update"),
        )
        return _agent_resource(self.service, revision.agent_id)

    async def clone_agent(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        source_revision = self.service.get_agent_revision(
            resource_ref,
            _optional_positive_int(payload, "revision"),
        )
        project_id = (
            source_revision.project_id
            if "project_id" not in payload
            else _optional_string(payload, "project_id")
        )
        workspace_id = (
            source_revision.workspace_id
            if "workspace_id" not in payload
            else _optional_string(payload, "workspace_id")
        )
        revision = self.service.clone_agent(
            resource_ref,
            revision=source_revision.revision,
            owner_ref=_provided_owner_ref(payload.get("owner_ref")),
            project_id=project_id,
            workspace_id=workspace_id,
            name=_optional_string(payload, "name"),
            provenance=_control_plane_provenance(context, "agent.clone"),
        )
        return _agent_resource(self.service, revision.agent_id)

    async def rollback_agent(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        revision = self.service.rollback_agent(
            resource_ref,
            _required_positive_int(payload, "target_revision"),
            expected_revision=_required_positive_int(payload, "expected_revision"),
            provenance=_control_plane_provenance(context, "agent.rollback"),
        )
        return _agent_resource(self.service, revision.agent_id)

    async def start_agent(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        runtime = self._require_runtime()
        task_id = _required_string(payload, "task_id")
        run_id = _required_string(payload, "run_id")
        environment = await self._execution_environment(
            context,
            resource_ref=resource_ref,
            task_id=task_id,
            run_id=run_id,
            is_team=False,
            payload=payload,
        )
        record = await runtime.start_agent(
            task_id=task_id,
            run_id=run_id,
            agent_id=resource_ref,
            revision=_optional_positive_int(payload, "revision"),
            mapper=self._select_mapper(payload),
            task_model_override=_optional_routing_requirements(payload, "task_model_override"),
            requested_capability_ids=_string_tuple(payload, "requested_capability_ids"),
            available_capability_ids=environment.available_capability_ids,
            granted_permissions=environment.granted_permissions,
            available_worker_capabilities=environment.available_worker_capabilities,
            task_context=_json_mapping(payload.get("task_context", {}), "task_context"),
            project_context=_json_mapping(payload.get("project_context", {}), "project_context"),
        )
        return _agent_run_resource(record)

    async def create_team(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, AGENT_TEAM_COLLECTION)
        revision = self.service.create_team(
            _team_profile_from_json(_required(payload, "profile")),
            owner_ref=_owner_ref(payload.get("owner_ref"), context),
            project_id=_optional_string(payload, "project_id"),
            workspace_id=_optional_string(payload, "workspace_id"),
            provenance=_control_plane_provenance(context, "agent-team.create"),
        )
        return _team_resource(self.service, revision.team_id)

    async def update_team(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current = self.service.repository.get_team(resource_ref)
        project_id = (
            current.project_id
            if "project_id" not in payload
            else _optional_string(payload, "project_id")
        )
        workspace_id = (
            current.workspace_id
            if "workspace_id" not in payload
            else _optional_string(payload, "workspace_id")
        )
        revision = self.service.update_team(
            resource_ref,
            _team_profile_from_json(_required(payload, "profile")),
            expected_revision=_required_positive_int(payload, "expected_revision"),
            owner_ref=_provided_owner_ref(payload.get("owner_ref")),
            project_id=project_id,
            workspace_id=workspace_id,
            provenance=_control_plane_provenance(context, "agent-team.update"),
        )
        return _team_resource(self.service, revision.team_id)

    async def clone_team(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        source_revision = self.service.get_team_revision(
            resource_ref,
            _optional_positive_int(payload, "revision"),
        )
        project_id = (
            source_revision.project_id
            if "project_id" not in payload
            else _optional_string(payload, "project_id")
        )
        workspace_id = (
            source_revision.workspace_id
            if "workspace_id" not in payload
            else _optional_string(payload, "workspace_id")
        )
        revision = self.service.clone_team(
            resource_ref,
            revision=source_revision.revision,
            owner_ref=_provided_owner_ref(payload.get("owner_ref")),
            project_id=project_id,
            workspace_id=workspace_id,
            name=_optional_string(payload, "name"),
            provenance=_control_plane_provenance(context, "agent-team.clone"),
        )
        return _team_resource(self.service, revision.team_id)

    async def rollback_team(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        revision = self.service.rollback_team(
            resource_ref,
            _required_positive_int(payload, "target_revision"),
            expected_revision=_required_positive_int(payload, "expected_revision"),
            provenance=_control_plane_provenance(context, "agent-team.rollback"),
        )
        return _team_resource(self.service, revision.team_id)

    async def start_team(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        runtime = self._require_runtime()
        task_id = _required_string(payload, "task_id")
        run_id = _required_string(payload, "run_id")
        environment = await self._execution_environment(
            context,
            resource_ref=resource_ref,
            task_id=task_id,
            run_id=run_id,
            is_team=True,
            payload=payload,
        )
        records = await runtime.start_team(
            task_id=task_id,
            run_id=run_id,
            team_id=resource_ref,
            revision=_optional_positive_int(payload, "revision"),
            mapper=self._select_mapper(payload),
            task_model_override=_optional_routing_requirements(payload, "task_model_override"),
            requested_capability_ids=_string_tuple(payload, "requested_capability_ids"),
            available_capability_ids=environment.available_capability_ids,
            granted_permissions=environment.granted_permissions,
            available_worker_capabilities=environment.available_worker_capabilities,
            task_context=_json_mapping(payload.get("task_context", {}), "task_context"),
            project_context=_json_mapping(payload.get("project_context", {}), "project_context"),
        )
        items: list[JsonValue] = [_agent_run_resource(record) for record in records]
        return {"team_id": resource_ref, "agent_runs": items}

    def _require_runtime(self) -> AgentRuntime:
        if self.runtime is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "Agent runtime commands are not enabled in this Control Plane composition",
            )
        return self.runtime

    def _select_mapper(
        self,
        payload: Mapping[str, object],
    ) -> AgentOrchestratorMapper | None:
        adapter_id = _optional_string(payload, "orchestrator_adapter_id")
        if adapter_id is None:
            return None
        if adapter_id == ReferenceOrchestratorMapper.adapter_id:
            return ReferenceOrchestratorMapper()
        try:
            return self.orchestrator_mappers[adapter_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"orchestrator adapter is not registered: {adapter_id}",
                details={"orchestrator_adapter_id": adapter_id},
            ) from exc

    async def _execution_environment(
        self,
        context: RequestContext,
        *,
        resource_ref: str,
        task_id: str,
        run_id: str,
        is_team: bool,
        payload: Mapping[str, object],
    ) -> AgentExecutionEnvironment:
        caller_supplied = sorted(_SERVER_RESOLVED_EXECUTION_FIELDS.intersection(payload))
        if caller_supplied:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "runtime availability and authorization fields are server-resolved",
                details={"fields": cast(JsonValue, caller_supplied)},
            )
        if self.execution_environment_resolver is None:
            return AgentExecutionEnvironment()
        return await self.execution_environment_resolver.resolve(
            context,
            resource_ref=resource_ref,
            task_id=task_id,
            run_id=run_id,
            is_team=is_team,
        )


def register_agent_control_plane(
    control_plane: ControlPlane,
    service: AgentService,
    *,
    runtime: AgentRuntime | None = None,
    orchestrator_mappers: Mapping[str, AgentOrchestratorMapper] | None = None,
    execution_environment_resolver: AgentExecutionEnvironmentResolver | None = None,
) -> None:
    """Register #33 resources without making Agents part of the #32 foundation."""

    control_plane.register_resource_service(AGENT_COLLECTION, AgentResourceService(service))
    control_plane.register_resource_service(
        AGENT_TEAM_COLLECTION,
        AgentTeamResourceService(service),
    )
    control_plane.register_resource_service(
        AGENT_RUN_COLLECTION,
        AgentRunResourceService(service),
    )
    handlers = AgentCommandHandlers(
        service,
        runtime,
        orchestrator_mappers=orchestrator_mappers,
        execution_environment_resolver=execution_environment_resolver,
    )
    control_plane.register_command("agent.create", handlers.create_agent)
    control_plane.register_command("agent.update", handlers.update_agent)
    control_plane.register_command("agent.clone", handlers.clone_agent)
    control_plane.register_command("agent.rollback", handlers.rollback_agent)
    if runtime is not None:
        control_plane.register_command("agent.start", handlers.start_agent)
    control_plane.register_command("agent-team.create", handlers.create_team)
    control_plane.register_command("agent-team.update", handlers.update_team)
    control_plane.register_command("agent-team.clone", handlers.clone_team)
    control_plane.register_command("agent-team.rollback", handlers.rollback_team)
    if runtime is not None:
        control_plane.register_command("agent-team.start", handlers.start_team)


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
        "created_at": definition.created_at.isoformat(),
        "updated_at": definition.updated_at.isoformat(),
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
        "created_at": definition.created_at.isoformat(),
        "updated_at": definition.updated_at.isoformat(),
        "revision": json_object(revision),
    }


def _agent_run_resource(record: object) -> dict[str, JsonValue]:
    payload = json_object(record)
    agent_run_id = payload.get("agent_run_id")
    if not isinstance(agent_run_id, str):
        raise TypeError("AgentRunRecord serialization requires agent_run_id")
    return {"id": agent_run_id, "type": "agent_run", **payload}


def _profile_from_json(value: object) -> AgentProfile:
    data = _mapping(value, "profile")
    instructions = _mapping(_required(data, "instructions"), "profile.instructions")
    model_data = _mapping(data.get("model", {}), "profile.model")
    capability_data = _mapping(data.get("capabilities", {}), "profile.capabilities")
    constraints_raw = capability_data.get("constraints", [])
    if not isinstance(constraints_raw, list | tuple):
        raise ValueError("profile.capabilities.constraints must be an array")
    data_access_raw = _mapping(data.get("data_access", {}), "profile.data_access")
    workspace_data = _mapping(data.get("workspace_defaults", {}), "profile.workspace_defaults")
    hooks_data = _mapping(data.get("policy_hooks", {}), "profile.policy_hooks")
    fallback_raw = _optional_string(model_data, "fallback") or ModelFallbackPolicy.FAIL.value

    return AgentProfile(
        name=_required_string(data, "name"),
        role=_required_string(data, "role"),
        instructions=AgentInstructions(
            role=_instruction_source(_required(instructions, "role")),
            platform_constraint_refs=_string_tuple(instructions, "platform_constraint_refs"),
            project_instruction_refs=_string_tuple(instructions, "project_instruction_refs"),
        ),
        description=_optional_string(data, "description") or "",
        model=AgentModelPolicy(
            requirements=_routing_requirements(
                model_data.get("requirements", {}),
                "profile.model.requirements",
            ),
            routing_profile_ref=_optional_string(model_data, "routing_profile_ref"),
            allow_task_override=_boolean(model_data, "allow_task_override", False),
            fallback=ModelFallbackPolicy(fallback_raw),
        ),
        capabilities=AgentCapabilityPolicy(
            allowed=_string_tuple(capability_data, "allowed"),
            denied=_string_tuple(capability_data, "denied"),
            constraints=tuple(_capability_constraint(item) for item in constraints_raw),
        ),
        data_access=AgentDataAccess(
            memory_scopes=tuple(
                MemoryScope(item) for item in _string_tuple(data_access_raw, "memory_scopes")
            ),
            memory_config_refs=_string_tuple(data_access_raw, "memory_config_refs"),
            knowledge_source_ids=_string_tuple(data_access_raw, "knowledge_source_ids"),
            allow_user_memory=_boolean(data_access_raw, "allow_user_memory", False),
        ),
        workspace_defaults=AgentWorkspaceDefaults(
            project_id=_optional_string(workspace_data, "project_id"),
            workspace_id=_optional_string(workspace_data, "workspace_id"),
        ),
        policy_hooks=AgentPolicyHooks(
            authorization_profile_ref=_optional_string(hooks_data, "authorization_profile_ref"),
            verification_policy_refs=_string_tuple(hooks_data, "verification_policy_refs"),
        ),
        resource_hints=_json_mapping(data.get("resource_hints", {}), "profile.resource_hints"),
        enabled=_boolean(data, "enabled", True),
        metadata=_json_mapping(data.get("metadata", {}), "profile.metadata"),
    )


def _team_profile_from_json(value: object) -> AgentTeamProfile:
    data = _mapping(value, "team profile")
    members_raw = data.get("members")
    if not isinstance(members_raw, list | tuple) or not members_raw:
        raise ValueError("team profile.members must be a non-empty array")
    policy_raw = (
        _optional_string(data, "unavailable_member_policy") or UnavailableMemberPolicy.FAIL.value
    )
    return AgentTeamProfile(
        name=_required_string(data, "name"),
        members=tuple(_team_member(item) for item in members_raw),
        description=_optional_string(data, "description") or "",
        coordination_policy_ref=_optional_string(data, "coordination_policy_ref"),
        leader_agent_id=_optional_string(data, "leader_agent_id"),
        shared_capability_ids=_string_tuple(data, "shared_capability_ids"),
        shared_resource_refs=_string_tuple(data, "shared_resource_refs"),
        max_parallel_agents=_optional_positive_int(data, "max_parallel_agents"),
        max_steps=_optional_positive_int(data, "max_steps"),
        unavailable_member_policy=UnavailableMemberPolicy(policy_raw),
        enabled=_boolean(data, "enabled", True),
        metadata=_json_mapping(data.get("metadata", {}), "team profile.metadata"),
    )


def _routing_requirements(value: object, name: str) -> RoutingRequirements:
    data = _mapping(value, name)
    return RoutingRequirements(
        explicit_model_id=_optional_string(data, "explicit_model_id"),
        min_context_window=_optional_positive_int(data, "min_context_window"),
        tool_calling=_boolean(data, "tool_calling", False),
        structured_output=_boolean(data, "structured_output", False),
        streaming=_boolean(data, "streaming", False),
        modalities=_string_tuple(data, "modalities"),
        reasoning=_string_tuple(data, "reasoning"),
        local_only=_boolean(data, "local_only", False),
        self_hosted_only=_boolean(data, "self_hosted_only", False),
    )


def _optional_routing_requirements(
    mapping: Mapping[str, object],
    name: str,
) -> RoutingRequirements | None:
    value = mapping.get(name)
    if value is None:
        return None
    return _routing_requirements(value, name)


def _instruction_source(value: object) -> InstructionSource:
    data = _mapping(value, "instruction source")
    return InstructionSource(
        content=_optional_string(data, "content"),
        ref=_optional_string(data, "ref"),
        version=_optional_string(data, "version"),
    )


def _capability_constraint(value: object) -> CapabilityConstraint:
    data = _mapping(value, "capability constraint")
    return CapabilityConstraint(
        capability_id=_required_string(data, "capability_id"),
        required=_boolean(data, "required", True),
        exact_version=_optional_string(data, "exact_version"),
        minimum_version=_optional_string(data, "minimum_version"),
        maximum_version=_optional_string(data, "maximum_version"),
        required_features=_string_tuple(data, "required_features"),
        approval_ref=_optional_string(data, "approval_ref"),
    )


def _team_member(value: object) -> AgentTeamMember:
    data = _mapping(value, "team member")
    agent_data = _mapping(_required(data, "agent"), "team member.agent")
    return AgentTeamMember(
        agent=AgentRevisionRef(
            agent_id=_required_string(agent_data, "agent_id"),
            revision=_required_positive_int(agent_data, "revision"),
        ),
        role=_required_string(data, "role"),
        required=_boolean(data, "required", True),
        can_delegate_to=_string_tuple(data, "can_delegate_to"),
    )


def _owner_ref(value: object | None, context: RequestContext) -> OwnerRef:
    if value is not None:
        parsed = _provided_owner_ref(value)
        assert parsed is not None
        return parsed
    if context.actor.owner_type is None or context.actor.owner_id is None:
        raise ValueError("owner_ref is required when actor owner context is unavailable")
    return OwnerRef(type=context.actor.owner_type, id=context.actor.owner_id)


def _provided_owner_ref(value: object | None) -> OwnerRef | None:
    if value is None:
        return None
    data = _mapping(value, "owner_ref")
    raw_type = _required_string(data, "type")
    if raw_type not in {"user", "organization", "team", "service"}:
        raise ValueError("owner_ref.type must be user, organization, team or service")
    owner_type = cast(Literal["user", "organization", "team", "service"], raw_type)
    return OwnerRef(type=owner_type, id=_required_string(data, "id"))


def _control_plane_provenance(context: RequestContext, operation: str) -> Provenance:
    return Provenance(
        source="control-plane",
        actor_ref=context.actor.principal_ref,
        details={
            "operation": operation,
            "request_id": context.request_id,
            "correlation_id": context.correlation_id,
        },
    )


def _require_collection(resource_ref: str, expected: str) -> None:
    if resource_ref != expected:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"create command resource_ref must be {expected!r}",
        )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def _json_mapping(value: object, name: str) -> Mapping[str, JsonValue]:
    return cast(Mapping[str, JsonValue], _mapping(value, name))


def _required(mapping: Mapping[str, object], name: str) -> object:
    if name not in mapping:
        raise ValueError(f"{name} is required")
    return mapping[name]


def _required_string(mapping: Mapping[str, object], name: str) -> str:
    value = mapping.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _optional_string(mapping: Mapping[str, object], name: str) -> str | None:
    value = mapping.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string or null")
    return value


def _boolean(mapping: Mapping[str, object], name: str, default: bool) -> bool:
    value = mapping.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value


def _required_positive_int(mapping: Mapping[str, object], name: str) -> int:
    value = mapping.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be an integer >= 1")
    return value


def _optional_positive_int(mapping: Mapping[str, object], name: str) -> int | None:
    value = mapping.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be an integer >= 1 or null")
    return value


def _string_tuple(mapping: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = mapping.get(name, [])
    if not isinstance(value, list | tuple):
        raise ValueError(f"{name} must be an array of strings")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"{name} must be an array of strings")
    items = tuple(cast(str, item) for item in value)
    if any(not item.strip() for item in items):
        raise ValueError(f"{name} must not contain blank strings")
    return items
