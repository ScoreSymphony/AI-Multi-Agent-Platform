"""Runtime Control Plane commands for Skill resolution and AgentRun evidence binding."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.models import ModelConfiguration

from .control_plane_helpers import (
    boolean,
    optional_positive_int,
    optional_string,
    required_string,
    revision_refs,
)
from .control_plane_resources import binding_resource, bundle_resource
from .resolver import SkillResolutionRequest
from .runtime import SkillExecutionCoordinator

_SERVER_RESOLVED_FIELDS = frozenset(
    {
        "allowed_capability_ids",
        "available_capability_versions",
        "granted_permissions",
        "available_worker_capabilities",
        "model_configuration",
    }
)


@dataclass(frozen=True, slots=True)
class SkillExecutionEnvironment:
    """Trusted runtime facts supplied by platform composition, never by the caller."""

    allowed_capability_ids: frozenset[str] = frozenset()
    available_capability_versions: Mapping[str, str] | None = None
    granted_permissions: frozenset[str] = frozenset()
    available_worker_capabilities: frozenset[str] = frozenset()
    model_configuration: ModelConfiguration | None = None


class SkillExecutionEnvironmentResolver(Protocol):
    async def resolve(
        self,
        context: RequestContext,
        *,
        agent_id: str,
        agent_revision: int,
        task_id: str,
        run_id: str,
    ) -> SkillExecutionEnvironment: ...


class SkillRuntimeCommands:
    def __init__(
        self,
        coordinator: SkillExecutionCoordinator,
        agents: AgentService,
        *,
        execution_environment_resolver: SkillExecutionEnvironmentResolver | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.agents = agents
        self.execution_environment_resolver = execution_environment_resolver

    async def resolve(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        caller_supplied = sorted(_SERVER_RESOLVED_FIELDS.intersection(payload))
        if caller_supplied:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Skill execution authorization/model fields are server-resolved",
                details={"fields": cast(JsonValue, caller_supplied)},
            )
        agent_revision = self.agents.get_agent_revision(
            resource_ref,
            optional_positive_int(payload, "agent_revision"),
        )
        task_id = required_string(payload, "task_id")
        run_id = required_string(payload, "run_id")
        environment = await self._environment(
            context,
            agent_id=agent_revision.agent_id,
            agent_revision=agent_revision.revision,
            task_id=task_id,
            run_id=run_id,
        )
        bundle, binding = self.coordinator.prepare(
            SkillResolutionRequest(
                run_id=run_id,
                task_id=task_id,
                agent_id=agent_revision.agent_id,
                agent_revision=agent_revision.revision,
                agent_role=agent_revision.profile.role,
                required_skills=revision_refs(payload, "required_skills"),
                explicit_skills=revision_refs(payload, "explicit_skills"),
                planner_skills=revision_refs(payload, "planner_skills"),
                default_skills=revision_refs(payload, "default_skills"),
                allowed_capability_ids=environment.allowed_capability_ids,
                available_capability_versions=environment.available_capability_versions,
                granted_permissions=environment.granted_permissions,
                available_worker_capabilities=environment.available_worker_capabilities,
                model_configuration=environment.model_configuration,
                step_id=optional_string(payload, "step_id"),
                project_id=agent_revision.project_id,
                workspace_id=agent_revision.workspace_id,
                allow_deprecated=boolean(payload, "allow_deprecated", False),
            )
        )
        return {
            "skill_bundle": bundle_resource(bundle),
            "binding": binding_resource(binding),
        }

    async def bind_agent_run(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        record = self.agents.repository.get_agent_run(required_string(payload, "agent_run_id"))
        return binding_resource(self.coordinator.bind_agent_run(resource_ref, record))

    async def _environment(
        self,
        context: RequestContext,
        *,
        agent_id: str,
        agent_revision: int,
        task_id: str,
        run_id: str,
    ) -> SkillExecutionEnvironment:
        if self.execution_environment_resolver is None:
            return SkillExecutionEnvironment()
        return await self.execution_environment_resolver.resolve(
            context,
            agent_id=agent_id,
            agent_revision=agent_revision,
            task_id=task_id,
            run_id=run_id,
        )
