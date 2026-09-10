"""Kernel-native Plan/Step Context fallback for canonical internal workflows.

The normal PlanStep Context source reads the Coordination projection. Some bounded platform-owned
workflows, including Verification repair, create their canonical Plan/Step directly through the
PlatformKernel and intentionally do not create a second Coordination lifecycle. This adapter keeps
Coordination authoritative whenever that projection exists and falls back to immutable kernel event
history only when the whole Coordination Plan is absent.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.coordination.repository import CoordinatorRepository
from ai_multi_agent_platform.kernel.repository import EventRepository, RunRepository, TaskRepository

from .models import (
    ContextCandidate,
    ContextDataClassification,
    ContextEntryRole,
    ContextSourceRef,
    ContextSourceRequest,
    ContextSourceType,
    ContextTrust,
)
from .source_adapters import PlanStepContextSourceAdapter


class KernelFallbackPlanStepContextSourceAdapter(PlanStepContextSourceAdapter):
    """Read Coordination Plan/Step Context first, then exact kernel history when absent.

    The fallback is deliberately narrow: only ``CoordinatorRepository.get_plan`` returning
    ``NOT_FOUND`` enables kernel resolution. If a Coordination Plan exists but its Step projection
    is missing or inconsistent, the base adapter still fails closed.
    """

    adapter_id = "platform.kernel-fallback-plan-step-context/v1"

    def __init__(
        self,
        coordinator: CoordinatorRepository,
        *,
        tasks: TaskRepository,
        events: EventRepository,
        runs: RunRepository | None = None,
    ) -> None:
        super().__init__(coordinator, runs=runs)
        self._tasks = tasks
        self._events = events

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        if request.plan_id is None or request.step_id is None:
            return ()
        try:
            self.coordinator.get_plan(request.plan_id)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
            return await self._collect_kernel_plan_step(request)
        return await super().collect(request)

    async def _collect_kernel_plan_step(
        self,
        request: ContextSourceRequest,
    ) -> tuple[ContextCandidate, ...]:
        if request.plan_id is None or request.step_id is None:
            return ()

        task = await self._tasks.get_task(request.task_id)
        if task.plan_ref != request.plan_id or request.step_id not in task.step_ids:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "requested kernel Plan/Step is not the active canonical Task Plan",
            )

        plan_events = tuple(
            event
            for event in await self._events.read_events(request.task_id)
            if event.event_type == "plan.created"
            and event.payload.get("plan_ref") == request.plan_id
        )
        if len(plan_events) != 1:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "active kernel Plan does not resolve to exactly one canonical plan.created event",
            )
        plan_event = plan_events[0]
        raw_steps = plan_event.payload.get("steps")
        if not isinstance(raw_steps, (tuple, list)):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical plan.created event has no Step payloads",
            )
        matches = tuple(
            item
            for item in raw_steps
            if isinstance(item, Mapping) and item.get("id") == request.step_id
        )
        if len(matches) != 1:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "active kernel Step does not resolve to exactly one canonical Step payload",
            )
        step = matches[0]
        title = step.get("title")
        objective = step.get("objective")
        raw_dependencies = step.get("depends_on", ())
        if not isinstance(title, str) or not isinstance(objective, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical kernel Step title/objective is malformed",
            )
        if not isinstance(raw_dependencies, (tuple, list)):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical kernel Step dependencies are malformed",
            )
        dependencies = tuple(item for item in raw_dependencies if isinstance(item, str))
        if len(dependencies) != len(raw_dependencies):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical kernel Step dependencies must be string IDs",
            )

        content = _canonical_json(
            {
                "plan_id": request.plan_id,
                "plan_event_id": plan_event.id,
                "step_id": request.step_id,
                "step_title": title,
                "step_objective": objective,
                "dependencies": dependencies,
                "projection": "kernel_event",
            }
        )
        digest = _digest(content)
        return (
            ContextCandidate(
                source=ContextSourceRef(
                    ContextSourceType.PLAN_STEP,
                    request.step_id,
                    revision=plan_event.id,
                    digest=digest,
                    locator=f"event:{plan_event.id}",
                ),
                role=ContextEntryRole.CONTEXT,
                selection_reason=(
                    "active canonical kernel Plan/Step purpose without a Coordination projection"
                ),
                inline_content=content,
                content_digest=digest,
                trust=ContextTrust.TRUSTED,
                data_classification=ContextDataClassification.INTERNAL,
                priority=80,
                relevance=1.0,
                project_id=task.task.project_id,
                conflict_key=f"step:{request.step_id}",
            ),
        )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _digest(content: str) -> str:
    return f"sha256:{sha256(content.encode('utf-8')).hexdigest()}"


__all__ = ["KernelFallbackPlanStepContextSourceAdapter"]
