"""Reference composition boundaries for platform-owned planning.

The helpers in this module deliberately keep planning away from Run execution while
making validated planning decisions effective at the existing canonical runtime seam.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import replace
from typing import Protocol

from ai_multi_agent_platform.agents.execution_profile import (
    AgentExecutionBinding,
    encode_agent_step_execution_bindings,
)
from ai_multi_agent_platform.agents.repository import AgentRepository
from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.contracts import (
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    HealthStatus,
    JsonValue,
    LifecycleBackend,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.domain import Plan, Step
from ai_multi_agent_platform.kernel.models import TaskState
from ai_multi_agent_platform.models import ModelRegistry, RoutingRequirements
from ai_multi_agent_platform.security import ActorIdentity, AuthorizationGate

from .environment import PlanningEnvironment, PlanningEnvironmentResolver
from .models import (
    PlanProposal,
    PlanningInventory,
    PlanningTrigger,
    ProposalRecord,
    ProposalStatus,
    ReplanPolicy,
)
from .providers import Planner
from .repository import PlanningRepository
from .service import (
    ActivatedPlanCoordinator,
    PlanningEventSink,
    PlanningKernel,
    PlanningService,
)


class PlanningOnlyLifecycleBackend(LifecycleBackend):
    """Kernel construction dependency that deliberately rejects all execution operations.

    #439 needs the existing ``PlatformKernel.plan_task`` path to allocate canonical Plan/Step
    identities. It must not gain a second execution path as a side effect. Public single-node
    composition therefore gives the planning kernel this backend: plan mutation remains available,
    while Run start/read/cancel through that kernel fails closed.
    """

    descriptor = ProviderDescriptor(
        provider_id="planning-only-lifecycle",
        provider_type="planning-boundary",
        supported_operations=(),
        capabilities=(
            Capability(
                name="planning.execution.forbidden",
                kind=CapabilityKind.EXECUTION,
                supported_operations=(),
            ),
        ),
        health=HealthStatus.HEALTHY,
        available=True,
    )

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        del request
        raise _execution_forbidden()

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        del run_id, context
        raise _execution_forbidden()

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        del run_id, context
        raise _execution_forbidden()


class StepBindingKernel(Protocol):
    """Narrow canonical Task mutation seam needed to persist Step execution bindings."""

    async def update_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        metadata: dict[str, JsonValue],
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> object: ...


class PlanCoordinator(Protocol):
    """Existing #384 registration seam; planning does not own progression."""

    async def register_plan(self, plan: Plan, steps: tuple[Step, ...]) -> object: ...


class PlanningBindingCoordinator:
    """Persist exact Step execution bindings before delegating activation to #384.

    Canonical Step IDs do not exist until ``plan.created`` is committed. The PlanningService
    therefore hands the reconstructed canonical Plan/Steps here while its proposal is still
    ``ACTIVATING``. This wrapper binds those IDs to the already validated Agent/model/capability
    requirements on the canonical Task and only then delegates to the durable coordinator.

    The Task update uses a proposal-scoped idempotency key, so a crash after binding but before
    #384 registration is restart-safe. Repeated registration after a fully activated proposal is
    also safe and resolves the proposal through its canonical activation Plan ID.
    """

    def __init__(
        self,
        *,
        repository: PlanningRepository,
        kernel: StepBindingKernel,
        delegate: PlanCoordinator,
    ) -> None:
        self._repository = repository
        self._kernel = kernel
        self._delegate = delegate

    async def register_plan(self, plan: Plan, steps: tuple[Step, ...]) -> object:
        record = self._proposal_for_plan(plan)
        proposal = record.proposal
        if len(proposal.steps) != len(steps):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "activated canonical Step count does not match planning proposal",
                details={
                    "proposal_id": proposal.proposal_id,
                    "proposal_steps": len(proposal.steps),
                    "canonical_steps": len(steps),
                },
            )

        bindings: dict[str, AgentExecutionBinding] = {}
        for draft, step in zip(proposal.steps, steps, strict=True):
            if draft.title != step.title:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "activated canonical Step order/title does not match planning proposal",
                    details={
                        "proposal_id": proposal.proposal_id,
                        "step_key": draft.key,
                        "canonical_step_id": step.id,
                    },
                )
            assignment = draft.assignment
            if assignment is None or assignment.agent_id is None:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "reference planning activation requires an exact Agent assignment per Step",
                    details={"proposal_id": proposal.proposal_id, "step_key": draft.key},
                )
            bindings[step.id] = AgentExecutionBinding(
                agent_id=assignment.agent_id,
                agent_revision=assignment.agent_revision,
                model_config_id=draft.model_requirements.explicit_model_id,
                model_requirements=(
                    draft.model_requirements
                    if _has_model_requirements(draft.model_requirements)
                    else None
                ),
                capability_ids=tuple(
                    requirement.capability_id
                    for requirement in draft.capability_requirements
                    if requirement.required
                ),
                workspace_id=draft.workspace_id,
                objective=draft.objective or draft.title,
                input_refs=draft.input_refs,
                output_refs=draft.output_refs,
                expected_evidence=draft.expected_evidence,
                verification_policy_refs=draft.verification_policy_refs,
            )

        await self._kernel.update_task(
            idempotency_key=f"planning:{proposal.proposal_id}:bind-steps",
            task_id=proposal.task_id,
            metadata=encode_agent_step_execution_bindings(bindings),
            actor_ref=proposal.planner.planner_id,
            source="platform-planning",
        )
        return await self._delegate.register_plan(plan, steps)

    def _proposal_for_plan(self, plan: Plan) -> ProposalRecord:
        records = self._repository.list_for_task(plan.task_id)
        activating = [
            record
            for record in records
            if record.status is ProposalStatus.ACTIVATING
            and record.proposal.plan_revision == plan.revision
        ]
        if len(activating) == 1:
            return activating[0]
        activated = [
            record
            for record in records
            if record.status is ProposalStatus.ACTIVATED
            and record.activation_plan_id == plan.id
            and record.proposal.plan_revision == plan.revision
        ]
        if len(activated) == 1:
            return activated[0]
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "cannot resolve one planning proposal for canonical Plan registration",
            details={"task_id": plan.task_id, "plan_id": plan.id, "plan_revision": plan.revision},
        )


class ReferencePlanningService(PlanningService):
    """Standard local profile with server-resolved planning and exact execution bindings.

    The generic planning contract intentionally permits exact Agents, Teams and role requirements.
    The current single-node lifecycle executes one Agent per canonical Step. Until a Team/role
    execution adapter is explicitly composed, activating such a proposal would silently discard
    planner intent. This reference service rejects that activation before any canonical Plan
    mutation while leaving the provider-neutral PlanningService contract unchanged.

    When ``environment_resolver`` is configured, planning inventory authority is resolved from
    trusted server state. Direct permission/worker claims are rejected and unauthorized Agent,
    Team or Capability revisions are removed before the planner sees the inventory.
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
        self._planning_environment: ContextVar[PlanningEnvironment | None] = ContextVar(
            f"planning-environment-{id(self)}",
            default=None,
        )

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
        granted_permissions: frozenset[str] = frozenset(),
        available_worker_capabilities: frozenset[str] = frozenset(),
        max_steps: int = 128,
        max_parallel_steps: int | None = None,
    ) -> ProposalRecord:
        resolver = self.environment_resolver
        if resolver is None:
            return await super().propose(
                task_id=task_id,
                idempotency_key=idempotency_key,
                trigger=trigger,
                reason=reason,
                workspace_id=workspace_id,
                evidence_refs=evidence_refs,
                task_constraints=task_constraints,
                granted_permissions=granted_permissions,
                available_worker_capabilities=available_worker_capabilities,
                max_steps=max_steps,
                max_parallel_steps=max_parallel_steps,
            )
        if granted_permissions or available_worker_capabilities:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "planning availability and authorization fields are server-resolved",
            )

        task = await self.kernel.get_task(task_id)
        environment = await resolver.resolve(
            task=task,
            context=self._operation_context(task, idempotency_key),
            workspace_id=workspace_id,
        )
        token = self._planning_environment.set(environment)
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
            self._planning_environment.reset(token)

    def _inventory(self, task: TaskState, workspace_id: str | None) -> PlanningInventory:
        inventory = super()._inventory(task, workspace_id)
        environment = self._planning_environment.get()
        if environment is None:
            return inventory

        statically_eligible_capabilities: frozenset[tuple[str, str]] = frozenset()
        if self.capabilities is not None:
            statically_eligible_capabilities = frozenset(
                (capability.capability_id, capability.version)
                for capability in self.capabilities.list_capabilities(
                    granted_permissions=environment.granted_permissions,
                    available_worker_capabilities=environment.available_worker_capabilities,
                    include_unavailable=False,
                )
            )
        allowed_capabilities = (
            environment.authorized_capability_versions & statically_eligible_capabilities
        )
        return replace(
            inventory,
            agents=tuple(
                candidate
                for candidate in inventory.agents
                if (candidate.agent_id, candidate.revision)
                in environment.authorized_agent_revisions
            ),
            teams=tuple(
                candidate
                for candidate in inventory.teams
                if (candidate.team_id, candidate.revision)
                in environment.authorized_team_revisions
            ),
            capabilities=tuple(
                candidate
                for candidate in inventory.capabilities
                if (candidate.capability_id, candidate.version) in allowed_capabilities
            ),
        )

    async def activate(
        self,
        proposal_id: str,
        *,
        idempotency_key: str,
        actor: ActorIdentity | None = None,
        approval_id: str | None = None,
    ) -> ProposalRecord:
        record = self.repository.get(proposal_id)
        _validate_reference_assignments(record.proposal)
        return await super().activate(
            proposal_id,
            idempotency_key=idempotency_key,
            actor=actor,
            approval_id=approval_id,
        )


def _validate_reference_assignments(proposal: PlanProposal) -> None:
    for step in proposal.steps:
        assignment = step.assignment
        if assignment is None or assignment.agent_id is None:
            kind = "unassigned"
            if assignment is not None and assignment.team_id is not None:
                kind = "Agent Team"
            elif assignment is not None and assignment.role_requirement is not None:
                kind = "role requirement"
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"reference single-node planning cannot execute {kind} Step assignment exactly",
                details={"proposal_id": proposal.proposal_id, "step_key": step.key},
            )
        if assignment.agent_revision is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "validated reference planning Agent assignment is missing its revision",
                details={"proposal_id": proposal.proposal_id, "step_key": step.key},
            )


def _has_model_requirements(requirements: RoutingRequirements) -> bool:
    return any(
        (
            requirements.explicit_model_id is not None,
            requirements.min_context_window is not None,
            requirements.tool_calling,
            requirements.structured_output,
            requirements.streaming,
            bool(requirements.modalities),
            bool(requirements.reasoning),
            requirements.local_only,
            requirements.self_hosted_only,
        )
    )


def _execution_forbidden() -> ContractError:
    return ContractError(
        ErrorCode.FORBIDDEN,
        "autonomous planning cannot execute or control canonical Runs",
        provider_id=PlanningOnlyLifecycleBackend.descriptor.provider_id,
    )


__all__ = [
    "PlanningBindingCoordinator",
    "PlanningOnlyLifecycleBackend",
    "ReferencePlanningService",
]
