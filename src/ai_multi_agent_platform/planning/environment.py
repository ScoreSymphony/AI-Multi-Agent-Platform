"""Server-owned planning environment and policy-aware planning facade for #439."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol

from ai_multi_agent_platform.agents.repository import AgentRepository
from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.kernel.models import TaskState
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.security import ActorIdentity, ActorType, AuthorizationGate

from .models import PlanningInventory, PlanningTrigger, ProposalRecord, ReplanPolicy
from .providers import Planner
from .repository import PlanningRepository
from .service import (
    ActivatedPlanCoordinator,
    PlanningEventSink,
    PlanningKernel,
    PlanningService as _BasePlanningService,
)


@dataclass(frozen=True, slots=True)
class PlanningEnvironment:
    """Trusted planning availability/authorization resolved by server composition.

    Permission and Worker-capability facts are consumed by canonical capability discovery.
    Optional allowsets let policy-aware compositions restrict the canonical Agent, Agent Team,
    Capability and model inventories visible to a planner. ``None`` means that the corresponding
    canonical inventory is not additionally restricted by this resolver; an empty set denies all.
    """

    granted_permissions: frozenset[str] = frozenset()
    available_worker_capabilities: frozenset[str] = frozenset()
    allowed_agent_ids: frozenset[str] | None = None
    allowed_team_ids: frozenset[str] | None = None
    allowed_capability_ids: frozenset[str] | None = None
    allowed_model_config_ids: frozenset[str] | None = None


class PlanningEnvironmentResolver(Protocol):
    """Resolve trusted planning facts from authenticated/canonical server context."""

    async def resolve(
        self,
        *,
        task: TaskState,
        context: OperationContext,
        actor: ActorIdentity,
        workspace_id: str | None,
        trigger: PlanningTrigger,
    ) -> PlanningEnvironment: ...


@dataclass(frozen=True, slots=True)
class _EnvironmentSnapshot:
    task_revision: int
    plan_ref: str | None
    environment: PlanningEnvironment


_ENVIRONMENT: ContextVar[_EnvironmentSnapshot | None] = ContextVar(
    "planning_environment",
    default=None,
)


class PlanningService(_BasePlanningService):
    """PlanningService facade whose authority inputs are resolved only by the server.

    The core planning engine still owns proposal validation/activation. This facade rejects
    caller-supplied permission and Worker-availability facts, resolves them via a server-injected
    ``PlanningEnvironmentResolver``, and filters planner inventory before the core engine sees it.
    Direct callers therefore cannot turn arbitrary strings into planning authority.
    """

    def __init__(
        self,
        *,
        planner: Planner,
        repository: PlanningRepository,
        kernel: PlanningKernel,
        agents: AgentRepository | None = None,
        capabilities: CapabilityRegistry | None = None,
        models: ModelRegistry | None = None,
        authorization: AuthorizationGate | None = None,
        coordinator: ActivatedPlanCoordinator | None = None,
        replan_policy: ReplanPolicy | None = None,
        event_sink: PlanningEventSink | None = None,
        environment_resolver: PlanningEnvironmentResolver | None = None,
    ) -> None:
        super().__init__(
            planner=planner,
            repository=repository,
            kernel=kernel,
            agents=agents,
            capabilities=capabilities,
            models=models,
            authorization=authorization,
            coordinator=coordinator,
            replan_policy=replan_policy,
            event_sink=event_sink,
        )
        self.environment_resolver = environment_resolver

    async def propose(
        self,
        *,
        task_id: str,
        idempotency_key: str,
        trigger: PlanningTrigger = PlanningTrigger.INITIAL,
        reason: str | None = None,
        workspace_id: str | None = None,
        evidence_refs: tuple[str, ...] = (),
        task_constraints: tuple[str, ...] = (),
        granted_permissions: frozenset[str] | None = None,
        available_worker_capabilities: frozenset[str] | None = None,
        max_steps: int = 128,
        max_parallel_steps: int | None = None,
        actor: ActorIdentity | None = None,
    ) -> ProposalRecord:
        """Create a proposal using server-resolved authorization and availability facts."""

        caller_authority_fields: list[str] = []
        if granted_permissions is not None:
            caller_authority_fields.append("granted_permissions")
        if available_worker_capabilities is not None:
            caller_authority_fields.append("available_worker_capabilities")
        if caller_authority_fields:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "planning authority and availability fields are resolved by the server",
                details={"fields": caller_authority_fields},
            )

        existing = self.repository.get_by_idempotency(task_id, idempotency_key)
        if existing is not None:
            return existing

        task = await self.kernel.get_task(task_id)
        resolved_actor = actor or ActorIdentity(
            actor_id=f"{task.task.owner_ref.type}:{task.task.owner_ref.id}",
            actor_type=ActorType.SERVICE,
        )
        context = self._operation_context(task, idempotency_key)
        environment = await self._resolve_environment(
            task=task,
            context=context,
            actor=resolved_actor,
            workspace_id=workspace_id,
            trigger=trigger,
        )
        token = _ENVIRONMENT.set(
            _EnvironmentSnapshot(
                task_revision=task.revision,
                plan_ref=task.plan_ref,
                environment=environment,
            )
        )
        try:
            return await super().propose(
                task_id=task_id,
                idempotency_key=idempotency_key,
                trigger=trigger,
                reason=reason,
                workspace_id=workspace_id,
                evidence_refs=evidence_refs,
                task_constraints=task_constraints,
                granted_permissions=environment.granted_permissions,
                available_worker_capabilities=environment.available_worker_capabilities,
                max_steps=max_steps,
                max_parallel_steps=max_parallel_steps,
            )
        finally:
            _ENVIRONMENT.reset(token)

    def _inventory(self, task: TaskState, workspace_id: str | None) -> PlanningInventory:
        inventory = super()._inventory(task, workspace_id)
        snapshot = _ENVIRONMENT.get()
        environment = PlanningEnvironment() if snapshot is None else snapshot.environment
        if snapshot is not None and (
            snapshot.task_revision != task.revision or snapshot.plan_ref != task.plan_ref
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "planning environment is stale against canonical Task/Plan state",
                details={
                    "environment_task_revision": snapshot.task_revision,
                    "current_task_revision": task.revision,
                    "environment_plan_ref": snapshot.plan_ref,
                    "current_plan_ref": task.plan_ref,
                },
            )

        usable_capabilities: set[tuple[str, str]] | None = None
        if self.capabilities is not None:
            usable_capabilities = {
                (spec.capability_id, spec.version)
                for spec in self.capabilities.list_capabilities(
                    granted_permissions=environment.granted_permissions,
                    available_worker_capabilities=environment.available_worker_capabilities,
                    include_unavailable=True,
                )
            }

        return PlanningInventory(
            agents=tuple(
                item
                for item in inventory.agents
                if environment.allowed_agent_ids is None
                or item.agent_id in environment.allowed_agent_ids
            ),
            teams=tuple(
                item
                for item in inventory.teams
                if environment.allowed_team_ids is None
                or item.team_id in environment.allowed_team_ids
            ),
            capabilities=tuple(
                item
                for item in inventory.capabilities
                if (
                    usable_capabilities is None
                    or (item.capability_id, item.version) in usable_capabilities
                )
                and (
                    environment.allowed_capability_ids is None
                    or item.capability_id in environment.allowed_capability_ids
                )
            ),
            models=tuple(
                item
                for item in inventory.models
                if environment.allowed_model_config_ids is None
                or item.model_config_id in environment.allowed_model_config_ids
            ),
        )

    async def _resolve_environment(
        self,
        *,
        task: TaskState,
        context: OperationContext,
        actor: ActorIdentity,
        workspace_id: str | None,
        trigger: PlanningTrigger,
    ) -> PlanningEnvironment:
        if self.environment_resolver is None:
            return PlanningEnvironment()
        environment = await self.environment_resolver.resolve(
            task=task,
            context=context,
            actor=actor,
            workspace_id=workspace_id,
            trigger=trigger,
        )
        if not isinstance(environment, PlanningEnvironment):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "planning environment resolver returned an invalid environment",
            )
        return environment


__all__ = [
    "PlanningEnvironment",
    "PlanningEnvironmentResolver",
    "PlanningService",
]