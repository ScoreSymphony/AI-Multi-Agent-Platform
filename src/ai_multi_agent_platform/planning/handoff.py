"""Canonical Plan reconstruction and durable coordinator handoff for planning activation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue, PlatformEvent
from ai_multi_agent_platform.domain import Plan, Provenance, Step, TaskStatus
from ai_multi_agent_platform.kernel.models import TaskState

from .models import PlanningTrigger, PlanProposal


class PlanningHandoffKernel(Protocol):
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


class ActivatedPlanCoordinator(Protocol):
    async def register_plan(self, plan: Plan, steps: tuple[Step, ...]) -> object: ...


class PlanningEmitter(Protocol):
    async def __call__(self, event_type: str, **attributes: JsonValue) -> None: ...


class PlanningActivationHandoff:
    """Resolve canonical activation provenance and hand the exact graph to coordination."""

    def __init__(
        self,
        *,
        kernel: PlanningHandoffKernel,
        coordinator: ActivatedPlanCoordinator | None,
    ) -> None:
        self.kernel = kernel
        self.coordinator = coordinator

    async def activated_plan_event(self, proposal: PlanProposal) -> PlatformEvent | None:
        history = await self.kernel.history(proposal.task_id)
        for event in reversed(history):
            if event.event_type != "plan.created":
                continue
            raw_metadata = event.payload.get("adapter_metadata")
            if not isinstance(raw_metadata, Mapping):
                continue
            planning_metadata = raw_metadata.get("platform-planning")
            if not isinstance(planning_metadata, Mapping):
                continue
            if planning_metadata.get("proposal_id") == proposal.proposal_id:
                return event
        return None

    async def ensure_ready(
        self,
        proposal: PlanProposal,
        event: PlatformEvent,
        *,
        emit: PlanningEmitter,
    ) -> None:
        if self.coordinator is None:
            return
        task = await self.kernel.get_task(proposal.task_id)
        if task.status in {TaskStatus.READY, TaskStatus.RUNNING}:
            return
        plan_id = self.plan_ref(event)
        if (
            task.status is TaskStatus.FAILED
            and proposal.trigger is not PlanningTrigger.INITIAL
            and proposal.base_plan_id is not None
            and task.plan_ref == plan_id
        ):
            actor_ref = None if event.provenance is None else event.provenance.actor_ref
            ready = await self.kernel.ready_task(
                idempotency_key=f"planning:{proposal.proposal_id}:ready-for-handoff",
                task_id=proposal.task_id,
                actor_ref=actor_ref,
                source="platform-planning",
            )
            if ready.status is not TaskStatus.READY:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "failed replacement replan did not restore Task ready state",
                    details={
                        "task_id": proposal.task_id,
                        "task_status": ready.status.value,
                        "proposal_id": proposal.proposal_id,
                    },
                )
            await emit(
                "planning.replan.task_reactivated",
                task_id=proposal.task_id,
                proposal_id=proposal.proposal_id,
                plan_id=plan_id,
                plan_revision=proposal.plan_revision,
                prior_plan_id=proposal.base_plan_id,
            )
            return
        raise ContractError(
            ErrorCode.CONFLICT,
            "canonical Plan cannot hand off to durable execution from current Task state",
            details={
                "task_id": proposal.task_id,
                "task_status": task.status.value,
                "proposal_id": proposal.proposal_id,
                "plan_id": plan_id,
            },
        )

    async def handoff(
        self,
        proposal: PlanProposal,
        event: PlatformEvent,
        *,
        emit: PlanningEmitter,
    ) -> None:
        if self.coordinator is None:
            return
        plan_id = self.plan_ref(event)
        raw_steps = event.payload.get("steps")
        if not isinstance(raw_steps, (list, tuple)):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "planning plan.created event is missing canonical Step payloads",
            )
        provenance = event.provenance or Provenance(source="platform-planning")
        task = await self.kernel.get_task(proposal.task_id)
        plan = Plan(
            id=plan_id,
            task_id=proposal.task_id,
            owner_ref=task.task.owner_ref,
            revision=proposal.plan_revision,
            active=True,
            project_id=task.task.project_id,
            created_at=event.occurred_at,
            provenance=provenance,
        )
        steps: list[Step] = []
        for raw in raw_steps:
            if not isinstance(raw, Mapping):
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "planning plan.created Step payload must be an object",
                )
            step_id = raw.get("id")
            title = raw.get("title")
            raw_depends_on = raw.get("depends_on", ())
            if (
                not isinstance(step_id, str)
                or not isinstance(title, str)
                or not isinstance(raw_depends_on, (list, tuple))
            ):
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "planning plan.created contains malformed canonical Step payload",
                )
            depends_on: list[str] = []
            for dependency in raw_depends_on:
                if not isinstance(dependency, str):
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "planning plan.created contains malformed canonical Step payload",
                    )
                depends_on.append(dependency)
            steps.append(
                Step(
                    id=step_id,
                    plan_id=plan_id,
                    title=title,
                    owner_ref=task.task.owner_ref,
                    depends_on=tuple(depends_on),
                    project_id=task.task.project_id,
                    created_at=event.occurred_at,
                    updated_at=event.occurred_at,
                    provenance=provenance,
                )
            )
        await self.coordinator.register_plan(plan, tuple(steps))
        await emit(
            "planning.coordination.handoff",
            task_id=proposal.task_id,
            proposal_id=proposal.proposal_id,
            plan_id=plan_id,
            plan_revision=proposal.plan_revision,
            step_count=len(steps),
        )

    @staticmethod
    def plan_ref(event: PlatformEvent) -> str:
        value = event.payload.get("plan_ref")
        if not isinstance(value, str) or not value.strip():
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "planning plan.created event is missing canonical plan_ref",
            )
        return value

    @staticmethod
    def failed_replan_can_activate(proposal: PlanProposal, task: TaskState) -> bool:
        return (
            task.status is TaskStatus.FAILED
            and proposal.trigger is not PlanningTrigger.INITIAL
            and proposal.base_plan_id is not None
            and task.plan_ref == proposal.base_plan_id
        )


__all__ = [
    "ActivatedPlanCoordinator",
    "PlanningActivationHandoff",
    "PlanningHandoffKernel",
]
