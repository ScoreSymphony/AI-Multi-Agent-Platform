"""Repository-backed Agent runtime integration for durable model-routing profiles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.models import (
    DeterministicModelRouter,
    ModelRegistry,
    ModelRoutingProfileRepository,
    ModelRoutingProfileResolver,
    ModelRoutingProfileRevision,
    RoutingRequirements,
)

from .models import AgentExecutionSpec, AgentRevision, AgentTeamRevision
from .runtime import AgentRuntime, _merge_requirements
from .service import AgentService


class DurableRoutingProfileAgentRuntime(AgentRuntime):
    """Resolve exact #309 policy revisions before canonical Agent model routing."""

    def __init__(
        self,
        service: AgentService,
        *,
        routing_profile_repository: ModelRoutingProfileRepository,
        model_registry: ModelRegistry | None = None,
        capability_registry: CapabilityRegistry | None = None,
    ) -> None:
        super().__init__(
            service,
            model_registry=model_registry,
            capability_registry=capability_registry,
        )
        self.routing_profile_resolver = ModelRoutingProfileResolver(routing_profile_repository)

    def prepare_agent(
        self,
        *,
        task_id: str,
        run_id: str,
        agent_id: str,
        revision: int | None = None,
        team_revision: AgentTeamRevision | None = None,
        task_model_override: RoutingRequirements | None = None,
        requested_capability_ids: tuple[str, ...] = (),
        shared_capability_ids: tuple[str, ...] = (),
        available_capability_ids: frozenset[str] = frozenset(),
        granted_permissions: frozenset[str] = frozenset(),
        available_worker_capabilities: frozenset[str] = frozenset(),
        task_context: Mapping[str, JsonValue] | None = None,
        project_context: Mapping[str, JsonValue] | None = None,
    ) -> AgentExecutionSpec:
        agent = self.service.get_agent_revision(agent_id, revision)
        if not agent.profile.enabled:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"agent is disabled: {agent.agent_id}@{agent.revision}",
            )

        requirements, profile = self._effective_profile_requirements(
            agent,
            task_model_override,
        )
        model_id, provider_id = self._resolve_profile_model(agent, requirements, profile)
        capability_ids, capability_versions = self._resolve_capabilities(
            agent,
            requested_capability_ids=requested_capability_ids,
            shared_capability_ids=shared_capability_ids,
            available_capability_ids=available_capability_ids,
            granted_permissions=granted_permissions,
            available_worker_capabilities=available_worker_capabilities,
        )
        return AgentExecutionSpec(
            task_id=task_id,
            run_id=run_id,
            agent_revision=agent,
            capability_ids=capability_ids,
            capability_versions=capability_versions,
            selected_model_config_id=model_id,
            selected_provider_id=provider_id,
            team_revision=team_revision,
            task_context=task_context or {},
            project_context=project_context or {},
        )

    def _effective_profile_requirements(
        self,
        agent: AgentRevision,
        task_override: RoutingRequirements | None,
    ) -> tuple[RoutingRequirements, ModelRoutingProfileRevision | None]:
        requirements = agent.profile.model.requirements
        profile: ModelRoutingProfileRevision | None = None
        profile_ref = agent.profile.model.routing_profile_ref
        if profile_ref is not None:
            profile = self.routing_profile_resolver.resolve(
                profile_ref,
                project_id=agent.project_id,
            )
            requirements = _merge_requirements(profile.policy.requirements, requirements)

        if task_override is not None:
            if not agent.profile.model.allow_task_override:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "task-level model override is not permitted by this Agent revision",
                )
            requirements = _merge_requirements(requirements, task_override)
        return requirements, profile

    def _resolve_profile_model(
        self,
        agent: AgentRevision,
        requirements: RoutingRequirements,
        profile: ModelRoutingProfileRevision | None,
    ) -> tuple[str | None, str | None]:
        if profile is None:
            return self._resolve_model(agent, requirements)
        if self.model_registry is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "agent routing profile requires a canonical ModelRegistry",
            )
        effective_profile = replace(
            profile,
            policy=replace(profile.policy, requirements=requirements),
        )
        route = DeterministicModelRouter(self.model_registry).route_profile(effective_profile)
        return route.model_config_id, route.provider_id


__all__ = ["DurableRoutingProfileAgentRuntime"]
