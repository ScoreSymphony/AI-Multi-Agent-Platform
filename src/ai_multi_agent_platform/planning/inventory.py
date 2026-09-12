"""Sanitized canonical inventory construction for platform-owned planning."""

from __future__ import annotations

from ai_multi_agent_platform.agents.models import UnavailableMemberPolicy
from ai_multi_agent_platform.agents.repository import AgentRepository
from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.contracts import ContractError, HealthStatus
from ai_multi_agent_platform.kernel.models import TaskState
from ai_multi_agent_platform.models import ModelRegistry

from .models import (
    PlanningAgentCandidate,
    PlanningCapabilityCandidate,
    PlanningInventory,
    PlanningModelCandidate,
    PlanningTeamCandidate,
)


class PlanningInventoryBuilder:
    """Build the provider-neutral inventory exposed to a planner.

    The builder owns scope filtering and canonical Agent/Team/capability/model projection only.
    Authorization-aware filtering performed by the reference composition remains layered on top of
    the resulting inventory, so trusted server-state resolution keeps its existing boundary.
    """

    def __init__(
        self,
        *,
        agents: AgentRepository | None = None,
        capabilities: CapabilityRegistry | None = None,
        models: ModelRegistry | None = None,
    ) -> None:
        self.agents = agents
        self.capabilities = capabilities
        self.models = models

    def build(self, task: TaskState, workspace_id: str | None) -> PlanningInventory:
        agent_candidates: list[PlanningAgentCandidate] = []
        team_candidates: list[PlanningTeamCandidate] = []
        capability_candidates: list[PlanningCapabilityCandidate] = []
        model_candidates: list[PlanningModelCandidate] = []

        if self.agents is not None:
            for agent_definition in self.agents.list_agents():
                agent_revision = self.agents.get_agent_revision(
                    agent_definition.agent_id,
                    agent_definition.current_revision,
                )
                if not self.scope_compatible(
                    agent_definition.project_id,
                    agent_definition.workspace_id,
                    task.task.project_id,
                    workspace_id,
                ):
                    continue
                agent_profile = agent_revision.profile
                agent_candidates.append(
                    PlanningAgentCandidate(
                        agent_id=agent_definition.agent_id,
                        revision=agent_revision.revision,
                        role=agent_profile.role,
                        enabled=agent_profile.enabled,
                        project_id=agent_definition.project_id,
                        workspace_id=agent_definition.workspace_id,
                        allowed_capability_ids=agent_profile.capabilities.allowed,
                        denied_capability_ids=agent_profile.capabilities.denied,
                        required_capability_ids=agent_profile.capabilities.required_ids,
                        model_requirements=agent_profile.model.requirements,
                        allow_task_model_override=agent_profile.model.allow_task_override,
                    )
                )
            for team_definition in self.agents.list_teams():
                team_revision = self.agents.get_team_revision(
                    team_definition.team_id,
                    team_definition.current_revision,
                )
                if not self.scope_compatible(
                    team_definition.project_id,
                    team_definition.workspace_id,
                    task.task.project_id,
                    workspace_id,
                ):
                    continue
                team_profile = team_revision.profile
                team_enabled = team_profile.enabled
                for member in team_profile.members:
                    try:
                        member_revision = self.agents.get_agent_revision(
                            member.agent.agent_id,
                            member.agent.revision,
                        )
                    except ContractError:
                        if (
                            member.required
                            or team_profile.unavailable_member_policy
                            is UnavailableMemberPolicy.FAIL
                        ):
                            team_enabled = False
                        continue
                    if not member_revision.profile.enabled and (
                        member.required
                        or team_profile.unavailable_member_policy is UnavailableMemberPolicy.FAIL
                    ):
                        team_enabled = False
                team_candidates.append(
                    PlanningTeamCandidate(
                        team_id=team_definition.team_id,
                        revision=team_revision.revision,
                        enabled=team_enabled,
                        member_agent_ids=tuple(
                            member.agent.agent_id for member in team_profile.members
                        ),
                        project_id=team_definition.project_id,
                        workspace_id=team_definition.workspace_id,
                        shared_capability_ids=team_profile.shared_capability_ids,
                        required_member_agent_ids=tuple(
                            member.agent.agent_id
                            for member in team_profile.members
                            if member.required
                        ),
                        skip_optional_unavailable=(
                            team_profile.unavailable_member_policy
                            is UnavailableMemberPolicy.SKIP_OPTIONAL
                        ),
                        max_parallel_agents=team_profile.max_parallel_agents,
                        max_steps=team_profile.max_steps,
                    )
                )

        if self.capabilities is not None:
            for spec in self.capabilities.inventory_capabilities(include_unavailable=True):
                capability_candidates.append(
                    PlanningCapabilityCandidate(
                        capability_id=spec.capability_id,
                        version=spec.version,
                        available=spec.available and spec.health is not HealthStatus.UNAVAILABLE,
                        features=spec.features,
                        required_permissions=spec.required_permissions,
                        required_approvals=spec.required_approvals,
                        safety=spec.safety.value,
                        side_effects=spec.side_effects.value,
                    )
                )

        if self.models is not None:
            for config in self.models.list_models():
                health = self.models.effective_health(config)
                caps = config.capabilities
                model_candidates.append(
                    PlanningModelCandidate(
                        model_config_id=config.config_id,
                        enabled=config.enabled,
                        available=(
                            config.enabled
                            and health in {HealthStatus.HEALTHY, HealthStatus.DEGRADED}
                        ),
                        location=config.location,
                        context_window=caps.context_window,
                        tool_calling=caps.tool_calling,
                        structured_output=caps.structured_output,
                        streaming=caps.streaming,
                        modalities=caps.modalities,
                        reasoning=caps.reasoning,
                    )
                )

        return PlanningInventory(
            agents=tuple(agent_candidates),
            teams=tuple(team_candidates),
            capabilities=tuple(capability_candidates),
            models=tuple(model_candidates),
        )

    @staticmethod
    def scope_compatible(
        candidate_project_id: str | None,
        candidate_workspace_id: str | None,
        task_project_id: str | None,
        workspace_id: str | None,
    ) -> bool:
        if candidate_project_id is not None and candidate_project_id != task_project_id:
            return False
        if candidate_workspace_id is not None and candidate_workspace_id != workspace_id:
            return False
        return True


__all__ = ["PlanningInventoryBuilder"]
