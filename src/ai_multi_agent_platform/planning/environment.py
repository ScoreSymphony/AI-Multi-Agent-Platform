"""Server-owned planning inventory authority and policy resolution.

Planning may inspect canonical resources, but it must never turn caller-supplied
availability or permission claims into runtime authority.  This module keeps the
trusted environment boundary explicit and provider-neutral.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from ai_multi_agent_platform.agents.repository import AgentRepository
from ai_multi_agent_platform.capabilities import (
    CapabilityRegistry,
    CapabilitySpec,
    CredentialRequirement,
    SafetyClassification,
    SideEffectClassification,
)
from ai_multi_agent_platform.contracts import (
    AuthorizationOutcome,
    OperationContext,
    normalize_authorization_decision,
)
from ai_multi_agent_platform.kernel.models import TaskState
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    ProposedAction,
    ResourceType,
)

PlanningPermissionResolver = Callable[[ActorIdentity, TaskState], frozenset[str]]
PlanningWorkerCapabilityResolver = Callable[[TaskState], frozenset[str]]


@dataclass(frozen=True, slots=True)
class PlanningEnvironment:
    """Trusted authority ceiling used to sanitize one planning request.

    Revision-qualified resource sets avoid accidentally authorizing a later Agent/Team
    revision that was not evaluated. Capability versions are qualified for the same
    reason. Empty sets are deliberately fail-closed.
    """

    granted_permissions: frozenset[str] = frozenset()
    available_worker_capabilities: frozenset[str] = frozenset()
    authorized_agent_revisions: frozenset[tuple[str, int]] = frozenset()
    authorized_team_revisions: frozenset[tuple[str, int]] = frozenset()
    authorized_capability_versions: frozenset[tuple[str, str]] = frozenset()


class PlanningEnvironmentResolver(Protocol):
    """Resolve trusted planning authority from canonical server-side state."""

    async def resolve(
        self,
        *,
        task: TaskState,
        context: OperationContext,
        workspace_id: str | None,
    ) -> PlanningEnvironment: ...


class PolicyAwarePlanningEnvironmentResolver:
    """Resolve planning candidates through canonical #15 authorization.

    Discovery calls the configured authorization *provider* directly instead of
    ``AuthorizationGate.decide`` so merely considering a candidate cannot create an
    Approval request. Capability decisions mirror canonical capability discovery:
    ``require_approval`` remains discoverable, while a denial is removed. Agent and
    Team assignments require an immediate allow because planning activation does not
    itself grant Agent/Team execution authority.

    Capability ``required_permissions`` are capability-local permission labels, not
    necessarily members of the canonical ``AuthorizationAction`` enum. They therefore
    become planning grants only after the server has authorized that exact capability
    candidate; they are never inferred from a caller or from global action vocabulary.
    """

    def __init__(
        self,
        *,
        agents: AgentRepository | None,
        capabilities: CapabilityRegistry | None,
        authorization: AuthorizationGate | None,
        permission_resolver: PlanningPermissionResolver | None = None,
        worker_capability_resolver: PlanningWorkerCapabilityResolver | None = None,
    ) -> None:
        self.agents = agents
        self.capabilities = capabilities
        self.authorization = authorization
        self.permission_resolver = permission_resolver
        self.worker_capability_resolver = worker_capability_resolver

    async def resolve(
        self,
        *,
        task: TaskState,
        context: OperationContext,
        workspace_id: str | None,
    ) -> PlanningEnvironment:
        del workspace_id
        actor = _task_actor(task)
        resolved_permissions = set(
            () if self.permission_resolver is None else self.permission_resolver(actor, task)
        )
        worker_capabilities = (
            frozenset()
            if self.worker_capability_resolver is None
            else frozenset(self.worker_capability_resolver(task))
        )

        if self.authorization is None:
            return PlanningEnvironment(
                granted_permissions=frozenset(resolved_permissions),
                available_worker_capabilities=worker_capabilities,
            )

        authorized_agents: set[tuple[str, int]] = set()
        authorized_teams: set[tuple[str, int]] = set()
        authorized_capabilities: set[tuple[str, str]] = set()

        if self.agents is not None:
            for agent_definition in self.agents.list_agents():
                agent_revision = self.agents.get_agent_revision(
                    agent_definition.agent_id,
                    agent_definition.current_revision,
                )
                if not agent_revision.profile.enabled:
                    continue
                action = ProposedAction(
                    AuthorizationContext(
                        actor=actor,
                        action=AuthorizationAction.EXECUTE,
                        resource_type=ResourceType.AGENT,
                        resource_id=agent_definition.agent_id,
                        operation=context,
                        task_id=task.task_id,
                        agent_id=agent_definition.agent_id,
                        side_effect="planning_candidate_discovery",
                    ),
                    payload={"agent_revision": agent_revision.revision},
                )
                if await self._allowed(action, allow_approval=False):
                    authorized_agents.add((agent_definition.agent_id, agent_revision.revision))

            for team_definition in self.agents.list_teams():
                team_revision = self.agents.get_team_revision(
                    team_definition.team_id,
                    team_definition.current_revision,
                )
                if not team_revision.profile.enabled:
                    continue
                action = ProposedAction(
                    AuthorizationContext(
                        actor=actor,
                        action=AuthorizationAction.EXECUTE,
                        resource_type=ResourceType.AGENT_TEAM,
                        resource_id=team_definition.team_id,
                        operation=context,
                        task_id=task.task_id,
                        side_effect="planning_candidate_discovery",
                    ),
                    payload={"team_revision": team_revision.revision},
                )
                if await self._allowed(action, allow_approval=False):
                    authorized_teams.add((team_definition.team_id, team_revision.revision))

        if self.capabilities is not None:
            for capability in self.capabilities.inventory_capabilities(include_unavailable=False):
                if not set(capability.required_worker_capabilities).issubset(worker_capabilities):
                    continue
                action = _capability_action(actor, task, context, capability)
                if await self._allowed(action, allow_approval=True):
                    authorized_capabilities.add((capability.capability_id, capability.version))
                    resolved_permissions.update(capability.required_permissions)

        return PlanningEnvironment(
            granted_permissions=frozenset(resolved_permissions),
            available_worker_capabilities=worker_capabilities,
            authorized_agent_revisions=frozenset(authorized_agents),
            authorized_team_revisions=frozenset(authorized_teams),
            authorized_capability_versions=frozenset(authorized_capabilities),
        )

    async def _allowed(self, action: ProposedAction, *, allow_approval: bool) -> bool:
        assert self.authorization is not None
        decision = normalize_authorization_decision(
            await self.authorization.provider.authorize(
                action.context.to_request(requested_action_digest=action.digest)
            )
        )
        if decision.outcome is AuthorizationOutcome.ALLOW:
            return True
        return allow_approval and decision.outcome is AuthorizationOutcome.REQUIRE_APPROVAL


def _task_actor(task: TaskState) -> ActorIdentity:
    owner = task.task.owner_ref
    actor_type = ActorType.HUMAN if owner.type == "user" else ActorType.SERVICE
    return ActorIdentity(actor_id=owner.id, actor_type=actor_type)


def _capability_action(
    actor: ActorIdentity,
    task: TaskState,
    context: OperationContext,
    capability: CapabilitySpec,
) -> ProposedAction:
    sensitive = (
        capability.safety is not SafetyClassification.STANDARD
        or capability.side_effects
        in {SideEffectClassification.EXTERNAL, SideEffectClassification.DESTRUCTIVE}
        or capability.credential_requirement is CredentialRequirement.REQUIRED
    )
    return ProposedAction(
        AuthorizationContext(
            actor=actor,
            action=(
                AuthorizationAction.INVOKE_SENSITIVE_CAPABILITY
                if sensitive
                else AuthorizationAction.EXECUTE
            ),
            resource_type=ResourceType.CAPABILITY,
            resource_id=capability.capability_id,
            operation=context,
            task_id=task.task_id,
            capability_ref=capability.capability_id,
            side_effect=capability.side_effects.value,
            security_labels=(capability.safety.value,),
        ),
        payload={"planning_discovery": True, "capability_version": capability.version},
    )


__all__ = [
    "PlanningEnvironment",
    "PlanningEnvironmentResolver",
    "PlanningPermissionResolver",
    "PlanningWorkerCapabilityResolver",
    "PolicyAwarePlanningEnvironmentResolver",
]
