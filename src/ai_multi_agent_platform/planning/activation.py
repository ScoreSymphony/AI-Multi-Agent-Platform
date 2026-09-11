"""Authorized canonical planning proposal activation."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    JsonValue,
    OperationContext,
    OperationControl,
    PlatformEvent,
    RetryMode,
)
from ai_multi_agent_platform.kernel.models import TaskState
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    AuthorizationOutcome,
    ProposedAction,
    ResourceType,
    RiskClassification,
)

from .handoff import ActivatedPlanCoordinator, PlanningActivationHandoff, PlanningEmitter
from .models import PriorPlanSnapshot, ProposalRecord, ProposalStatus
from .repository import PlanningRepository, advance_record


class PlanningActivationKernel(Protocol):
    async def get_task(self, task_id: str) -> TaskState: ...

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


class PriorPlanResolver(Protocol):
    async def __call__(self, task: TaskState) -> PriorPlanSnapshot | None: ...


class PlanningProposalActivation:
    """Authorize and commit one immutable proposal through canonical kernel ownership."""

    def __init__(
        self,
        *,
        repository: PlanningRepository,
        kernel: PlanningActivationKernel,
        authorization: AuthorizationGate | None,
        coordinator: ActivatedPlanCoordinator | None,
    ) -> None:
        self.repository = repository
        self.kernel = kernel
        self.authorization = authorization
        self.handoff = PlanningActivationHandoff(kernel=kernel, coordinator=coordinator)
        self.coordinator = coordinator

    async def activate(
        self,
        proposal_id: str,
        *,
        idempotency_key: str,
        actor: ActorIdentity | None,
        approval_id: str | None,
        prior_plan: PriorPlanResolver,
        emit: PlanningEmitter,
    ) -> ProposalRecord:
        if not idempotency_key.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "activation idempotency key is required")
        record = self.repository.get(proposal_id)
        proposal = record.proposal
        activated_event = await self.handoff.activated_plan_event(proposal)
        if activated_event is not None:
            if record.status not in {ProposalStatus.ACTIVATING, ProposalStatus.ACTIVATED}:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "canonical Plan exists for proposal that never entered authorized activation",
                    details={
                        "proposal_id": proposal_id,
                        "proposal_status": record.status.value,
                    },
                )
            plan_id = self.handoff.plan_ref(activated_event)
            if record.status is ProposalStatus.ACTIVATED and record.activation_plan_id == plan_id:
                return record
            await self.handoff.ensure_ready(proposal, activated_event, emit=emit)
            await self.handoff.handoff(proposal, activated_event, emit=emit)
            saved = advance_record(
                record,
                status=ProposalStatus.ACTIVATED,
                activation_plan_id=plan_id,
            )
            return self.repository.save(saved, expected_revision=record.revision)

        if record.status is ProposalStatus.INVALID:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "invalid planning proposal cannot be activated",
                details={"proposal_id": proposal_id, "errors": list(record.validation.errors)},
            )
        if record.status in {ProposalStatus.REJECTED, ProposalStatus.SUPERSEDED}:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"proposal {proposal_id} is {record.status.value}",
            )
        task = await self.kernel.get_task(proposal.task_id)
        if task.revision != proposal.task_revision or task.plan_ref != proposal.base_plan_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "planning proposal is stale against canonical Task/Plan state",
                details={
                    "proposal_id": proposal_id,
                    "proposal_task_revision": proposal.task_revision,
                    "current_task_revision": task.revision,
                    "proposal_base_plan_id": proposal.base_plan_id,
                    "current_plan_id": task.plan_ref,
                },
            )
        if (
            self.coordinator is not None
            and task.status.value not in {"ready", "running"}
            and not self.handoff.failed_replan_can_activate(proposal, task)
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "Plan activation with durable execution handoff requires Task ready/running state "
                "or an eligible failed replacement replan",
                details={"task_id": proposal.task_id, "task_status": task.status.value},
            )
        if proposal.base_plan_id is not None:
            prior = await prior_plan(task)
            if prior is not None and prior.running_step_ids:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "cannot activate a replacement Plan while prior Steps are running",
                    details={"running_step_ids": list(prior.running_step_ids)},
                )

        resolved_actor = actor or ActorIdentity(
            actor_id=f"{task.task.owner_ref.type}:{task.task.owner_ref.id}",
            actor_type=ActorType.SERVICE,
        )
        action = self.activation_action(task, record, resolved_actor)
        if record.validation.approval_required:
            if self.authorization is None:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "planning proposal introduces approval-gated capabilities but no approval "
                    "authority is configured",
                    details={"proposal_id": proposal_id},
                )
            if approval_id is None or not self.authorization.approvals.valid_for(
                approval_id, action
            ):
                pending = await self.authorization.ensure_pending_approval_with_event(
                    action,
                    reason="planning proposal introduces approval-gated capability requirements",
                    policy_id="planning:capability-requirements",
                    risk=RiskClassification.ELEVATED,
                )
                if (
                    record.status is not ProposalStatus.AWAITING_APPROVAL
                    or record.approval_id != pending.approval_id
                ):
                    updated = advance_record(
                        record,
                        status=ProposalStatus.AWAITING_APPROVAL,
                        approval_id=pending.approval_id,
                    )
                    self.repository.save(updated, expected_revision=record.revision)
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "planning proposal requires approval before activation",
                    details={
                        "proposal_id": proposal_id,
                        "approval_id": pending.approval_id,
                        "proposal_digest": proposal.digest,
                    },
                )

        if self.authorization is not None:
            decision = await self.authorization.decide(action, approval_id=approval_id)
            if decision.outcome is not AuthorizationOutcome.ALLOW:
                approval_ref = decision.constraints.get("approval_id")
                if decision.outcome is AuthorizationOutcome.REQUIRE_APPROVAL:
                    approval_value = approval_ref if isinstance(approval_ref, str) else None
                    if record.status is not ProposalStatus.AWAITING_APPROVAL:
                        updated = advance_record(
                            record,
                            status=ProposalStatus.AWAITING_APPROVAL,
                            approval_id=approval_value,
                        )
                        self.repository.save(updated, expected_revision=record.revision)
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    decision.reason or "planning proposal activation is not authorized",
                    details={
                        "proposal_id": proposal_id,
                        "authorization_outcome": decision.outcome.value,
                        "approval_id": approval_ref,
                    },
                )

        if record.status is not ProposalStatus.ACTIVATING:
            activating = advance_record(record, status=ProposalStatus.ACTIVATING)
            record = self.repository.save(activating, expected_revision=record.revision)
        await emit(
            "planning.activation.started",
            task_id=proposal.task_id,
            proposal_id=proposal_id,
            plan_revision=proposal.plan_revision,
            base_plan_id=proposal.base_plan_id,
        )
        result = await self.kernel.plan_task(
            idempotency_key=f"planning:{proposal_id}:activate",
            task_id=proposal.task_id,
            actor_ref=resolved_actor.actor_id,
            source="platform-planning",
        )
        if result.plan_ref is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "kernel activated planning proposal without a canonical Plan reference",
            )
        activated_event = await self.handoff.activated_plan_event(proposal)
        if activated_event is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "activated proposal is missing its canonical plan.created provenance event",
            )
        await self.handoff.ensure_ready(proposal, activated_event, emit=emit)
        await self.handoff.handoff(proposal, activated_event, emit=emit)
        activated = advance_record(
            record,
            status=ProposalStatus.ACTIVATED,
            activation_plan_id=result.plan_ref,
        )
        saved = self.repository.save(activated, expected_revision=record.revision)
        reused_step_ids: list[JsonValue] = [
            step_id
            for step_id in sorted(
                {step_id for step in proposal.steps for step_id in step.reuse_step_ids}
            )
        ]
        await emit(
            "planning.revision.activated",
            task_id=proposal.task_id,
            proposal_id=proposal_id,
            plan_id=result.plan_ref,
            plan_revision=proposal.plan_revision,
            base_plan_id=proposal.base_plan_id,
            reused_step_ids=reused_step_ids,
        )
        return saved

    def activation_action(
        self,
        task: TaskState,
        record: ProposalRecord,
        actor: ActorIdentity,
    ) -> ProposedAction:
        proposal = record.proposal
        return ProposedAction(
            AuthorizationContext(
                actor=actor,
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.TASK,
                resource_id=proposal.task_id,
                operation=self.operation_context(
                    task,
                    f"planning:{proposal.proposal_id}:authorize",
                ),
                task_id=proposal.task_id,
                side_effect="plan_revision_activation",
            ),
            payload={
                "proposal_id": proposal.proposal_id,
                "proposal_digest": proposal.digest,
                "plan_revision": proposal.plan_revision,
                "base_plan_id": proposal.base_plan_id,
                "trigger": proposal.trigger.value,
            },
        )

    @staticmethod
    def operation_context(task: TaskState, key: str) -> OperationContext:
        return OperationContext(
            correlation_id=task.task_id,
            causation_id=key,
            owner_type=task.task.owner_ref.type,
            owner_id=task.task.owner_ref.id,
            project_id=task.task.project_id,
            control=OperationControl(idempotency_key=key, retry_mode=RetryMode.IDEMPOTENT),
        )


__all__ = ["PlanningActivationKernel", "PlanningProposalActivation"]
