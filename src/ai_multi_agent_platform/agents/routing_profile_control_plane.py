"""#309-aware Agent Control Plane assignment authorization."""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.models import (
    ModelRoutingProfileAssignmentGate,
    ModelRoutingProfileRef,
)

from .control_plane import (
    AGENT_COLLECTION,
    AGENT_RUN_COLLECTION,
    AGENT_TEAM_COLLECTION,
    AgentCommandHandlers,
    AgentExecutionEnvironmentResolver,
    AgentResourceService,
    AgentRunResourceService,
    AgentTeamResourceService,
    _optional_positive_int,
    _optional_string,
    _profile_from_json,
    _required,
    _required_positive_int,
)
from .models import AgentProfile
from .runtime import AgentOrchestratorMapper, AgentRuntime
from .service import AgentService


class RoutingProfileAwareAgentCommandHandlers(AgentCommandHandlers):
    """Authorize exact #309 profile assignment before mutating canonical Agents."""

    def __init__(
        self,
        service: AgentService,
        assignment_gate: ModelRoutingProfileAssignmentGate,
        runtime: AgentRuntime | None = None,
        *,
        orchestrator_mappers: Mapping[str, AgentOrchestratorMapper] | None = None,
        execution_environment_resolver: AgentExecutionEnvironmentResolver | None = None,
    ) -> None:
        super().__init__(
            service,
            runtime,
            orchestrator_mappers=orchestrator_mappers,
            execution_environment_resolver=execution_environment_resolver,
        )
        self.assignment_gate = assignment_gate

    async def create_agent(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        profile = _profile_from_json(_required(payload, "profile"))
        project_id = _optional_string(payload, "project_id")
        await self._authorize_assignment(context, profile, project_id=project_id)
        return await super().create_agent(context, resource_ref, payload)

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
        profile = _profile_from_json(_required(payload, "profile"))
        await self._authorize_assignment(context, profile, project_id=project_id)
        return await super().update_agent(context, resource_ref, payload)

    async def clone_agent(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        source = self.service.get_agent_revision(
            resource_ref,
            _optional_positive_int(payload, "revision"),
        )
        project_id = (
            source.project_id
            if "project_id" not in payload
            else _optional_string(payload, "project_id")
        )
        await self._authorize_assignment(context, source.profile, project_id=project_id)
        return await super().clone_agent(context, resource_ref, payload)

    async def rollback_agent(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        target_revision = _required_positive_int(payload, "target_revision")
        historical = self.service.get_agent_revision(resource_ref, target_revision)
        await self._authorize_assignment(
            context,
            historical.profile,
            project_id=historical.project_id,
        )
        return await super().rollback_agent(context, resource_ref, payload)

    async def _authorize_assignment(
        self,
        context: RequestContext,
        profile: AgentProfile,
        *,
        project_id: str | None,
    ) -> None:
        raw_ref = profile.model.routing_profile_ref
        if raw_ref is None:
            return
        try:
            ref = ModelRoutingProfileRef.parse(raw_ref)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Agent routing profile assignment must pin an exact canonical revision",
                details={"routing_profile_ref": raw_ref},
            ) from exc
        await self.assignment_gate.authorize(
            ref,
            principal_ref=context.actor.principal_ref,
            context=OperationContext(
                correlation_id=context.correlation_id,
                causation_id=context.request_id,
                owner_type=context.actor.owner_type,
                owner_id=context.actor.owner_id,
                project_id=project_id,
            ),
        )


def register_routing_profile_aware_agent_control_plane(
    control_plane: ControlPlane,
    service: AgentService,
    assignment_gate: ModelRoutingProfileAssignmentGate,
    *,
    runtime: AgentRuntime | None = None,
    orchestrator_mappers: Mapping[str, AgentOrchestratorMapper] | None = None,
    execution_environment_resolver: AgentExecutionEnvironmentResolver | None = None,
) -> None:
    """Register #33 resources with #309 assignment authorization at mutation boundaries."""

    control_plane.register_resource_service(AGENT_COLLECTION, AgentResourceService(service))
    control_plane.register_resource_service(
        AGENT_TEAM_COLLECTION,
        AgentTeamResourceService(service),
    )
    control_plane.register_resource_service(
        AGENT_RUN_COLLECTION,
        AgentRunResourceService(service),
    )
    handlers = RoutingProfileAwareAgentCommandHandlers(
        service,
        assignment_gate,
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


__all__ = [
    "RoutingProfileAwareAgentCommandHandlers",
    "register_routing_profile_aware_agent_control_plane",
]
