"""#651 late-bound Handoff consumer evaluation through the canonical #903 matcher."""

from __future__ import annotations

from ai_multi_agent_platform.agents import (
    AgentCandidateKind,
    AgentCapabilityRequirement,
    AgentMatchingRequirements,
    AgentRepository,
    AgentResolver,
)
from ai_multi_agent_platform.agents.models import AgentRevisionRef, AgentTeamRevisionRef

from .models import ParticipantRef


class CanonicalConsumerRequirementEvaluator:
    """Evaluate Handoff consumer requirements without a second assignment implementation.

    The Handoff already supplies the concrete consumer at consumption time. Therefore the shared
    resolver receives that exact revision pin and performs eligibility checks only; it never scans
    or ranks unrelated Agents for this path.
    """

    def __init__(
        self,
        agents: AgentRepository,
        *,
        resolver: AgentResolver | None = None,
    ) -> None:
        # Preserve the public #651 composition seam while delegating matching to #903.
        self.agents = agents
        self._resolver = resolver or AgentResolver(agents)

    def accepts(self, requirements: tuple[str, ...], consumer: ParticipantRef) -> bool:
        parsed = self._parse(requirements, consumer)
        if parsed is None:
            return False
        result = self._resolver.resolve(parsed)
        return result.selected == consumer

    @staticmethod
    def _parse(
        requirements: tuple[str, ...],
        consumer: ParticipantRef,
    ) -> AgentMatchingRequirements | None:
        if not requirements:
            return None
        roles: list[str] = []
        capabilities: list[AgentCapabilityRequirement] = []
        policies: list[str] = []
        required_agent_id: str | None = None
        required_team_id: str | None = None
        for requirement in requirements:
            key, separator, value = requirement.partition(":")
            if not separator or not value:
                return None
            if key == "role":
                roles.append(value)
            elif key == "capability":
                capabilities.append(AgentCapabilityRequirement(value))
            elif key == "policy":
                policies.append(value)
            elif key == "agent":
                if required_agent_id is not None and required_agent_id != value:
                    return None
                required_agent_id = value
            elif key == "team":
                if required_team_id is not None and required_team_id != value:
                    return None
                required_team_id = value
            else:
                return None

        if isinstance(consumer, AgentRevisionRef):
            if required_team_id is not None:
                return None
            if required_agent_id is not None and required_agent_id != consumer.agent_id:
                return None
            return AgentMatchingRequirements(
                required_roles=tuple(dict.fromkeys(roles)),
                required_capabilities=tuple(capabilities),
                required_policy_refs=tuple(dict.fromkeys(policies)),
                exact_agent=consumer,
                candidate_kinds=(AgentCandidateKind.AGENT,),
            )

        if required_agent_id is not None:
            return None
        if required_team_id is not None and required_team_id != consumer.team_id:
            return None
        return AgentMatchingRequirements(
            required_roles=tuple(dict.fromkeys(roles)),
            required_capabilities=tuple(capabilities),
            required_policy_refs=tuple(dict.fromkeys(policies)),
            exact_team=AgentTeamRevisionRef(consumer.team_id, consumer.revision),
            candidate_kinds=(AgentCandidateKind.TEAM,),
        )


__all__ = ["CanonicalConsumerRequirementEvaluator"]
