"""Deterministic policy-scoped reviewer discovery for automatic Verification (#759).

This module deliberately does not perform global Agent discovery. A policy must provide an
explicit set of stable Agent and/or AgentTeam identities that form the only candidate scope.
Resolution pins the current Agent/Team revisions before execution and fails closed unless exactly
one enabled candidate satisfies the requested role/capability criteria.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.agents import AgentRuntime
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import validate_id

from .agent_workflow import ResolvedReviewerAssignment, ReviewerAssignmentResolver
from .models import VerificationRequest


@dataclass(frozen=True, slots=True)
class ReviewerDiscoverySelector:
    """Bounded reviewer discovery criteria owned by one policy stage.

    Candidate IDs are stable canonical identities. Their current revisions are resolved once at
    dispatch time and returned as exact pinned revisions. At least one candidate identity and at
    least one semantic selector (role and/or capability) are required so this boundary can never
    become an implicit global Reviewer fallback.
    """

    candidate_agent_ids: tuple[str, ...] = ()
    candidate_team_ids: tuple[str, ...] = ()
    reviewer_role: str | None = None
    required_capability_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.candidate_agent_ids and not self.candidate_team_ids:
            raise ValueError("reviewer discovery requires an explicit Agent/Team candidate scope")
        if self.reviewer_role is None and not self.required_capability_ids:
            raise ValueError("reviewer discovery requires a role and/or capability selector")
        if self.reviewer_role is not None and not self.reviewer_role.strip():
            raise ValueError("reviewer discovery role must not be blank")
        if len(set(self.candidate_agent_ids)) != len(self.candidate_agent_ids):
            raise ValueError("reviewer discovery Agent candidate IDs must be unique")
        if len(set(self.candidate_team_ids)) != len(self.candidate_team_ids):
            raise ValueError("reviewer discovery Team candidate IDs must be unique")
        if len(set(self.required_capability_ids)) != len(self.required_capability_ids):
            raise ValueError("reviewer discovery capability IDs must be unique")
        for agent_id in self.candidate_agent_ids:
            validate_id(agent_id, "agent")
        for team_id in self.candidate_team_ids:
            validate_id(team_id, "team")
        for capability_id in self.required_capability_ids:
            if not capability_id.strip():
                raise ValueError("reviewer discovery capability ID must not be blank")


class CapabilityRoleReviewerResolver(ReviewerAssignmentResolver):
    """Resolve one exact reviewer from an explicit policy-scoped candidate set."""

    def __init__(
        self,
        selectors: dict[tuple[str, int, str], ReviewerDiscoverySelector],
    ) -> None:
        self._selectors = dict(selectors)

    def resolve(
        self,
        request: VerificationRequest,
        agents: AgentRuntime,
    ) -> ResolvedReviewerAssignment:
        key = (request.policy_id, request.policy_version, request.stage_id)
        try:
            selector = self._selectors[key]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "no scoped reviewer discovery selector is configured for verification stage",
                details={
                    "policy_id": request.policy_id,
                    "policy_version": request.policy_version,
                    "stage_id": request.stage_id,
                },
            ) from exc

        matches = self._matches(selector, agents)
        if len(matches) != 1:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "scoped reviewer discovery must resolve exactly one enabled candidate",
                details={
                    "policy_id": request.policy_id,
                    "policy_version": request.policy_version,
                    "stage_id": request.stage_id,
                    "match_count": len(matches),
                    "candidate_agent_count": len(selector.candidate_agent_ids),
                    "candidate_team_count": len(selector.candidate_team_ids),
                    "reviewer_role": selector.reviewer_role,
                    "required_capability_ids": list(selector.required_capability_ids),
                },
            )
        return matches[0]

    @staticmethod
    def _matches(
        selector: ReviewerDiscoverySelector,
        agents: AgentRuntime,
    ) -> tuple[ResolvedReviewerAssignment, ...]:
        repository = agents.service.repository
        matches: list[ResolvedReviewerAssignment] = []

        for agent_id in selector.candidate_agent_ids:
            try:
                agent_definition = repository.get_agent(agent_id)
                revision = repository.get_agent_revision(
                    agent_id,
                    agent_definition.current_revision,
                )
            except ContractError as exc:
                if exc.code is ErrorCode.NOT_FOUND:
                    continue
                raise
            if not revision.profile.enabled:
                continue
            if not _role_matches(selector.reviewer_role, revision.profile.role):
                continue
            if not _capabilities_match(
                selector.required_capability_ids,
                revision.profile.capabilities.allowed,
                tuple(item.capability_id for item in revision.profile.capabilities.constraints),
            ):
                continue
            matches.append(
                ResolvedReviewerAssignment(
                    agent_id=revision.agent_id,
                    agent_revision=revision.revision,
                )
            )

        for team_id in selector.candidate_team_ids:
            try:
                team_definition = repository.get_team(team_id)
                team = repository.get_team_revision(
                    team_id,
                    team_definition.current_revision,
                )
            except ContractError as exc:
                if exc.code is ErrorCode.NOT_FOUND:
                    continue
                raise
            if not team.profile.enabled:
                continue

            for member in team.profile.members:
                try:
                    revision = repository.get_agent_revision(
                        member.agent.agent_id,
                        member.agent.revision,
                    )
                except ContractError as exc:
                    if exc.code is ErrorCode.NOT_FOUND:
                        continue
                    raise
                if not revision.profile.enabled:
                    continue
                if not _team_role_matches(
                    selector.reviewer_role,
                    member.role,
                    revision.profile.role,
                ):
                    continue
                if not _capabilities_match(
                    selector.required_capability_ids,
                    revision.profile.capabilities.allowed,
                    tuple(item.capability_id for item in revision.profile.capabilities.constraints),
                    team.profile.shared_capability_ids,
                ):
                    continue
                matches.append(
                    ResolvedReviewerAssignment(
                        agent_id=revision.agent_id,
                        agent_revision=revision.revision,
                        team_id=team.team_id,
                        team_revision=team.revision,
                    )
                )

        return tuple(matches)


def _role_matches(requested: str | None, actual: str) -> bool:
    if requested is None:
        return True
    return requested.strip().casefold() == actual.strip().casefold()


def _team_role_matches(
    requested: str | None,
    member_role: str,
    agent_role: str,
) -> bool:
    if requested is None:
        return True
    normalized = requested.strip().casefold()
    return normalized in {member_role.strip().casefold(), agent_role.strip().casefold()}


def _capabilities_match(
    required: tuple[str, ...],
    *available_sets: tuple[str, ...],
) -> bool:
    if not required:
        return True
    available = {item for values in available_sets for item in values}
    return set(required).issubset(available)


__all__ = [
    "CapabilityRoleReviewerResolver",
    "ReviewerDiscoverySelector",
]
