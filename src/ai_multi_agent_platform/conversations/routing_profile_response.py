"""Conversation routing backed by exact durable model-routing profile revisions."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.agents.models import AgentModelPolicy
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.models import (
    DeterministicModelRouter,
    ModelRoutingProfileRepository,
    ModelRoutingProfileResolver,
    ModelRuntime,
)

from .model_runtime_response import (
    ConversationInstructionResolver,
    ModelRuntimeConversationResponseProvider,
    _merge_requirements,
    _preference_requirements,
    _routing_requirements_json,
)
from .responses import ConversationResponseRequest


class DurableRoutingProfileConversationResponseProvider(ModelRuntimeConversationResponseProvider):
    """Use #309 as the routing-policy source of truth for interactive responses."""

    def __init__(
        self,
        runtime: ModelRuntime,
        agents: AgentService,
        *,
        routing_profile_repository: ModelRoutingProfileRepository,
        instruction_resolver: ConversationInstructionResolver | None = None,
    ) -> None:
        super().__init__(
            runtime,
            agents,
            instruction_resolver=instruction_resolver,
        )
        self._routing_profile_resolver = ModelRoutingProfileResolver(routing_profile_repository)

    def _effective_routing(
        self,
        agent_policy: AgentModelPolicy | None,
        request: ConversationResponseRequest,
    ) -> tuple[str | None, dict[str, JsonValue]]:
        if agent_policy is None or agent_policy.routing_profile_ref is None:
            model_id, requirements_json = super()._effective_routing(agent_policy, request)
            return model_id, requirements_json

        profile = self._routing_profile_resolver.resolve(
            agent_policy.routing_profile_ref,
            project_id=self._target_project_id(request),
        )
        requirements = _merge_requirements(
            profile.policy.requirements,
            agent_policy.requirements,
        )
        preference = request.model_preference
        if preference is not None and (
            preference.model_config_id is not None or preference.routing_requirements
        ):
            requirements = _merge_requirements(
                requirements,
                _preference_requirements(
                    preference.model_config_id,
                    preference.routing_requirements,
                ),
            )

        effective_profile = replace(
            profile,
            policy=replace(profile.policy, requirements=requirements),
        )
        route = DeterministicModelRouter(self._runtime.registry).route_profile(effective_profile)
        return route.model_config_id, _routing_requirements_json(requirements)

    def _target_project_id(self, request: ConversationResponseRequest) -> str | None:
        target = request.target
        if target.kind == "agent":
            return self._agents.get_agent_revision(target.id, target.revision).project_id
        if target.kind == "agent_team":
            team = self._agents.get_team_revision(target.id, target.revision)
            return self._team_leader(team).project_id
        return request.project_id


__all__ = ["DurableRoutingProfileConversationResponseProvider"]
