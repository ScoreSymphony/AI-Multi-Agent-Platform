"""Stable planning façade over responsibility-focused internal components."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from ai_multi_agent_platform.agents.repository import AgentRepository
from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    JsonValue,
    OperationContext,
    PlatformEvent,
)
from ai_multi_agent_platform.kernel.models import RunState, TaskState
from ai_multi_agent_platform.models import ModelRegistry, RoutingRequirements
from ai_multi_agent_platform.security import ActorIdentity, AuthorizationGate, ProposedAction

from .activation import PlanningProposalActivation
from .handoff import ActivatedPlanCoordinator, PlanningActivationHandoff
from .inventory import PlanningInventoryBuilder
from .models import (
    PlannerOutput,
    PlanningCapabilityCandidate,
    PlanningInventory,
    PlanningModelCandidate,
    PlanningRequest,
    PlanningStepDraft,
    PlanningTrigger,
    PlanProposal,
    PriorPlanSnapshot,
    ProposalRecord,
    ProposalStatus,
    ProposalValidation,
    ReplanPolicy,
)
from .proposals import PlanningProposalFactory
from .providers import Planner
from .replanning import PlanningReplanSupport
from .repository import PlanningRepository, advance_record
from .validation import PlanningProposalValidator

PlanningEventSink = Callable[[str, dict[str, JsonValue]], Awaitable[None] | None]


class PlanningKernel(Protocol):
    """Narrow canonical kernel surface required by planning."""

    async def get_task(self, task_id: str) -> TaskState: ...

    async def get_run(self, task_id: str, run_id: str) -> RunState: ...

    async def history(self, task_id: str) -> tuple[PlatformEvent, ...]: ...

    async def ready_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState: ...

    async def plan_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState: ...


class PlanningService:
    """Create, validate and activate immutable Plan proposals without executing Steps.

    This module is intentionally the stable planning façade. Inventory construction, proposal
    validation, immutable proposal construction, bounded replanning, authorization/activation and
    canonical coordinator handoff live in focused components. The façade retains the historical
    protected seams used by reference composition and compatibility tests, but those seams delegate
    rather than reimplementing the responsibilities.
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
    ) -> None:
        self.planner = planner
        self.repository = repository
        self.kernel = kernel
        self.agents = agents
        self.capabilities = capabilities
        self.models = models
        self.authorization = authorization
        self.coordinator = coordinator
        self.replan_policy = replan_policy or ReplanPolicy()
        self._event_sink = event_sink

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
        """Create one durable proposal for one canonical trigger, idempotently."""

        if not idempotency_key.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "planning idempotency key is required")
        if trigger is not PlanningTrigger.INITIAL and (reason is None or not reason.strip()):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "replanning requires a canonical trigger reason",
            )
        existing = self.repository.get_by_idempotency(task_id, idempotency_key)
        if existing is not None:
            return existing

        task = await self.kernel.get_task(task_id)
        prior_plan = await self._prior_plan(task)
        if trigger is PlanningTrigger.INITIAL and prior_plan is not None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "initial planning cannot replace an existing canonical Plan; use a replan trigger",
            )
        if trigger is not PlanningTrigger.INITIAL and prior_plan is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "replanning requires an existing canonical Plan",
            )
        fingerprint = self._trigger_fingerprint(
            task=task,
            trigger=trigger,
            reason=reason,
            evidence_refs=evidence_refs,
        )
        duplicate_trigger = self.repository.get_by_trigger(task_id, fingerprint)
        if duplicate_trigger is not None:
            return duplicate_trigger
        self._enforce_replan_budget(task_id, trigger)

        request = PlanningRequest(
            task_id=task_id,
            task_revision=task.revision,
            objective=task.task.description,
            context=self._operation_context(task, idempotency_key),
            inventory=self._inventory(task, workspace_id),
            trigger=trigger,
            reason=reason,
            workspace_id=workspace_id,
            prior_plan=prior_plan,
            evidence_refs=evidence_refs,
            task_constraints=task_constraints,
            granted_permissions=granted_permissions,
            available_worker_capabilities=available_worker_capabilities,
            max_steps=max_steps,
            max_parallel_steps=max_parallel_steps,
        )
        await self._emit(
            "planning.requested",
            task_id=task_id,
            trigger=trigger.value,
            task_revision=task.revision,
            base_plan_id=task.plan_ref,
        )
        output = await self.planner.propose(request)
        proposal = self._proposal(request, output)
        validation = self.validate(proposal, request)
        status = ProposalStatus.VALIDATED if validation.valid else ProposalStatus.INVALID
        record = ProposalRecord(
            proposal=proposal,
            status=status,
            idempotency_key=idempotency_key,
            validation=validation,
            trigger_fingerprint=fingerprint,
        )
        stored = self.repository.create(record)
        if stored.proposal.proposal_id != proposal.proposal_id:
            return stored
        await self._emit(
            "planning.proposal.created",
            task_id=task_id,
            proposal_id=proposal.proposal_id,
            proposal_digest=proposal.digest,
            plan_revision=proposal.plan_revision,
            planner_id=proposal.planner.planner_id,
            planner_kind=proposal.planner.kind.value,
            model_config_id=proposal.model_config_id,
        )
        await self._emit(
            "planning.validation.completed",
            task_id=task_id,
            proposal_id=proposal.proposal_id,
            valid=validation.valid,
            approval_required=validation.approval_required,
            error_count=len(validation.errors),
            warning_count=len(validation.warnings),
        )
        return stored

    def validate(self, proposal: PlanProposal, request: PlanningRequest) -> ProposalValidation:
        return PlanningProposalValidator().validate(proposal, request)

    async def activate(
        self,
        proposal_id: str,
        *,
        idempotency_key: str,
        actor: ActorIdentity | None = None,
        approval_id: str | None = None,
    ) -> ProposalRecord:
        return await self._activation_service().activate(
            proposal_id,
            idempotency_key=idempotency_key,
            actor=actor,
            approval_id=approval_id,
            prior_plan=self._prior_plan,
            emit=self._emit,
        )

    async def reject(self, proposal_id: str, *, reason: str | None = None) -> ProposalRecord:
        record = self.repository.get(proposal_id)
        if record.status is ProposalStatus.ACTIVATED:
            raise ContractError(ErrorCode.CONFLICT, "activated proposal cannot be rejected")
        if record.status is ProposalStatus.REJECTED:
            return record
        rejected = advance_record(
            record,
            status=ProposalStatus.REJECTED,
            failure_reason=reason or "rejected by authorized operator",
        )
        saved = self.repository.save(rejected, expected_revision=record.revision)
        await self._emit(
            "planning.proposal.rejected",
            task_id=record.proposal.task_id,
            proposal_id=proposal_id,
        )
        return saved

    def history(self, task_id: str) -> tuple[ProposalRecord, ...]:
        return self.repository.list_for_task(task_id)

    def _inventory(self, task: TaskState, workspace_id: str | None) -> PlanningInventory:
        return PlanningInventoryBuilder(
            agents=self.agents,
            capabilities=self.capabilities,
            models=self.models,
        ).build(task, workspace_id)

    def _proposal(self, request: PlanningRequest, output: PlannerOutput) -> PlanProposal:
        return PlanningProposalFactory().build(request, output)

    async def _prior_plan(self, task: TaskState) -> PriorPlanSnapshot | None:
        return await self._replan_support().prior_plan(task)

    def _replan_support(self) -> PlanningReplanSupport:
        return PlanningReplanSupport(
            repository=self.repository,
            kernel=self.kernel,
            policy=self.replan_policy,
        )

    def _enforce_replan_budget(self, task_id: str, trigger: PlanningTrigger) -> None:
        self._replan_support().enforce_budget(task_id, trigger)

    @staticmethod
    def _trigger_fingerprint(
        *,
        task: TaskState,
        trigger: PlanningTrigger,
        reason: str | None,
        evidence_refs: tuple[str, ...],
    ) -> str:
        return PlanningReplanSupport.trigger_fingerprint(
            task=task,
            trigger=trigger,
            reason=reason,
            evidence_refs=evidence_refs,
        )

    def _activation_service(self) -> PlanningProposalActivation:
        return PlanningProposalActivation(
            repository=self.repository,
            kernel=self.kernel,
            authorization=self.authorization,
            coordinator=self.coordinator,
        )

    def _activation_handoff(self) -> PlanningActivationHandoff:
        return PlanningActivationHandoff(
            kernel=self.kernel,
            coordinator=self.coordinator,
        )

    async def _activated_plan_event(self, proposal: PlanProposal) -> PlatformEvent | None:
        return await self._activation_handoff().activated_plan_event(proposal)

    @staticmethod
    def _plan_ref(event: PlatformEvent) -> str:
        return PlanningActivationHandoff.plan_ref(event)

    @staticmethod
    def _failed_replan_can_activate(proposal: PlanProposal, task: TaskState) -> bool:
        return PlanningActivationHandoff.failed_replan_can_activate(proposal, task)

    async def _ensure_handoff_ready(
        self,
        proposal: PlanProposal,
        event: PlatformEvent,
    ) -> None:
        await self._activation_handoff().ensure_ready(proposal, event, emit=self._emit)

    async def _handoff_to_coordinator(
        self,
        proposal: PlanProposal,
        event: PlatformEvent,
    ) -> None:
        await self._activation_handoff().handoff(proposal, event, emit=self._emit)

    def _activation_action(
        self,
        task: TaskState,
        record: ProposalRecord,
        actor: ActorIdentity,
    ) -> ProposedAction:
        return self._activation_service().activation_action(task, record, actor)

    def _operation_context(self, task: TaskState, key: str) -> OperationContext:
        return PlanningProposalActivation.operation_context(task, key)

    @staticmethod
    def _scope_compatible(
        candidate_project_id: str | None,
        candidate_workspace_id: str | None,
        task_project_id: str | None,
        workspace_id: str | None,
    ) -> bool:
        return PlanningInventoryBuilder.scope_compatible(
            candidate_project_id,
            candidate_workspace_id,
            task_project_id,
            workspace_id,
        )

    @staticmethod
    def _has_model_requirements(requirements: RoutingRequirements) -> bool:
        return PlanningProposalValidator.has_model_requirements(requirements)

    @staticmethod
    def _model_matches(
        candidate: PlanningModelCandidate,
        requirements: RoutingRequirements,
    ) -> bool:
        return PlanningProposalValidator.model_matches(candidate, requirements)

    @staticmethod
    def _has_cycle(steps: tuple[PlanningStepDraft, ...]) -> bool:
        return PlanningProposalValidator.has_cycle(steps)

    @staticmethod
    def _peak_parallelism(steps: tuple[PlanningStepDraft, ...]) -> int:
        return PlanningProposalValidator.peak_parallelism(steps)

    @staticmethod
    def _contains_provider_private_metadata(metadata: object) -> bool:
        return PlanningProposalValidator.contains_provider_private_metadata(metadata)

    @staticmethod
    def _capability_map(
        inventory: PlanningInventory,
    ) -> dict[str, PlanningCapabilityCandidate]:
        return PlanningProposalValidator.capability_map(inventory)

    async def _emit(self, event_type: str, **attributes: JsonValue) -> None:
        if self._event_sink is None:
            return
        result = self._event_sink(event_type, dict(attributes))
        if result is not None:
            await result


__all__ = [
    "ActivatedPlanCoordinator",
    "PlanningEventSink",
    "PlanningKernel",
    "PlanningService",
]
