"""Prior-plan reconstruction and bounded replanning policy for platform planning."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, PlatformEvent
from ai_multi_agent_platform.domain import RunStatus
from ai_multi_agent_platform.kernel.models import RunState, TaskState

from .models import (
    PlanningTrigger,
    PriorPlanSnapshot,
    ProposalStatus,
    ReplanPolicy,
)
from .repository import PlanningRepository


class ReplanningKernel(Protocol):
    async def get_run(self, task_id: str, run_id: str) -> RunState: ...

    async def history(self, task_id: str) -> tuple[PlatformEvent, ...]: ...


class PlanningReplanSupport:
    """Own prior-plan reconstruction, trigger identity and bounded replan accounting."""

    def __init__(
        self,
        *,
        repository: PlanningRepository,
        kernel: ReplanningKernel,
        policy: ReplanPolicy,
    ) -> None:
        self.repository = repository
        self.kernel = kernel
        self.policy = policy

    async def prior_plan(self, task: TaskState) -> PriorPlanSnapshot | None:
        if task.plan_ref is None:
            return None
        completed: list[str] = []
        running: list[str] = []
        failed: list[str] = []
        not_started: list[str] = []
        for step_id in task.step_ids:
            runs: list[RunState] = []
            for run_id in task.run_ids:
                run = await self.kernel.get_run(task.task_id, run_id)
                if run.run.subject_type == "step" and run.run.subject_id == step_id:
                    runs.append(run)
            if not runs:
                not_started.append(step_id)
                continue
            latest = max(runs, key=lambda item: (item.attempt, item.run.created_at))
            if latest.status is RunStatus.SUCCEEDED:
                completed.append(step_id)
            elif latest.status in {RunStatus.QUEUED, RunStatus.STARTING, RunStatus.RUNNING}:
                running.append(step_id)
            else:
                failed.append(step_id)
        history = await self.kernel.history(task.task_id)
        revision = sum(event.event_type == "plan.created" for event in history)
        return PriorPlanSnapshot(
            plan_id=task.plan_ref,
            revision=max(revision, 1),
            completed_step_ids=tuple(completed),
            running_step_ids=tuple(running),
            failed_step_ids=tuple(failed),
            not_started_step_ids=tuple(not_started),
            result_refs=task.result_ids,
            artifact_refs=task.artifact_ids,
        )

    def enforce_budget(self, task_id: str, trigger: PlanningTrigger) -> None:
        if trigger is PlanningTrigger.INITIAL:
            return
        used = sum(
            record.proposal.trigger is not PlanningTrigger.INITIAL
            and record.status is not ProposalStatus.REJECTED
            for record in self.repository.list_for_task(task_id)
        )
        if used >= self.policy.max_replans:
            raise ContractError(
                ErrorCode.RESOURCE_EXHAUSTED,
                "bounded replanning budget exhausted",
                details={
                    "task_id": task_id,
                    "max_replans": self.policy.max_replans,
                    "used_replans": used,
                },
            )

    @staticmethod
    def trigger_fingerprint(
        *,
        task: TaskState,
        trigger: PlanningTrigger,
        reason: str | None,
        evidence_refs: tuple[str, ...],
    ) -> str:
        payload = {
            "task_id": task.task_id,
            "task_revision": task.revision,
            "plan_id": task.plan_ref,
            "trigger": trigger.value,
            "reason": reason,
            "evidence_refs": sorted(evidence_refs),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


__all__ = ["PlanningReplanSupport", "ReplanningKernel"]
