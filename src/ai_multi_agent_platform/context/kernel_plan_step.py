"""Kernel-native Plan/Step Context fallback for canonical internal workflows.

The normal PlanStep Context source reads the Coordination projection. Some bounded platform-owned
workflows, including Verification repair, create their canonical Plan/Step directly through the
PlatformKernel and intentionally do not create a second Coordination lifecycle. This adapter keeps
Coordination authoritative whenever that projection exists and falls back to immutable kernel event
history only when the whole Coordination Plan is absent.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256

from ai_multi_agent_platform.agents.execution_profile import decode_agent_step_execution_binding
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.coordination.async_repository import runtime_coordinator_repository
from ai_multi_agent_platform.coordination.repository import CoordinatorRepository
from ai_multi_agent_platform.kernel.repository import EventRepository, RunRepository, TaskRepository

from .models import (
    ContextCandidate,
    ContextDataClassification,
    ContextEntryRole,
    ContextSourceRef,
    ContextSourceType,
    ContextTrust,
)
from .resolver import ContextSourceRequest
from .source_adapters import PlanStepContextSourceAdapter


class KernelFallbackPlanStepContextSourceAdapter(PlanStepContextSourceAdapter):
    """Read Coordination Plan/Step Context first, then exact kernel history when absent.

    The fallback is deliberately narrow: only ``CoordinatorRepository.get_plan`` returning
    ``NOT_FOUND`` enables kernel resolution. If a Coordination Plan exists but its Step projection
    is missing or inconsistent, the adapter still fails closed.
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
        self._runtime_coordinator = runtime_coordinator_repository(coordinator)
        self._tasks = tasks
        self._events = events

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        if request.plan_id is None or request.step_id is None:
            return ()
        try:
            await self._runtime_coordinator.get_plan(request.plan_id)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
            return await self._collect_kernel_plan_step(request)
        return await self._collect_coordination_plan_step(request)

    async def _collect_coordination_plan_step(
        self,
        request: ContextSourceRequest,
    ) -> tuple[ContextCandidate, ...]:
        assert request.plan_id is not None
        assert request.step_id is not None
        state = await self._runtime_coordinator.get_plan(request.plan_id)
        record = await self._runtime_coordinator.get_step_record(request.step_id)
        step = next((item for item in state.steps if item.id == request.step_id), None)
        if step is None:
            raise ContractError(ErrorCode.NOT_FOUND, "canonical Step is not in requested Plan")
        content = _canonical_json(
            {
                "plan_id": state.plan.id,
                "plan_revision": state.plan.revision,
                "plan_store_revision": state.store_revision,
                "step_id": step.id,
                "step_title": step.title,
                "step_status": step.status.value,
                "dependencies": list(step.depends_on),
                "coordination_revision": record.revision,
                "coordination_phase": record.phase.value,
            }
        )
        digest = _digest(content)
        candidates: list[ContextCandidate] = [
            ContextCandidate(
                source=ContextSourceRef(
                    ContextSourceType.PLAN_STEP,
                    step.id,
                    revision=(
                        f"plan:{state.plan.revision};store:{state.store_revision};"
                        f"step:{record.revision}"
                    ),
                    digest=digest,
                ),
                role=ContextEntryRole.CONTEXT,
                selection_reason="current canonical Plan/Step purpose and dependency state",
                inline_content=content,
                content_digest=digest,
                trust=ContextTrust.TRUSTED,
                data_classification=ContextDataClassification.INTERNAL,
                priority=80,
                relevance=1.0,
                project_id=step.project_id,
                conflict_key=f"step:{step.id}",
            )
        ]
        if self.runs is None:
            return tuple(candidates)
        for dependency_id in sorted(step.depends_on):
            dependency = await self._runtime_coordinator.get_step_record(dependency_id)
            if dependency.latest_run_id is None:
                continue
            prior = await self.runs.get_run(request.task_id, dependency.latest_run_id)
            prior_content = _canonical_json(
                {
                    "run_id": prior.run_id,
                    "step_id": dependency_id,
                    "status": prior.status.value,
                    "output": prior.output,
                    "result_ids": list(prior.result_ids),
                    "artifact_ids": list(prior.artifact_ids),
                    "revision": prior.revision,
                }
            )
            prior_digest = _digest(prior_content)
            candidates.append(
                ContextCandidate(
                    source=ContextSourceRef(
                        ContextSourceType.PRIOR_RUN,
                        prior.run_id,
                        revision=str(prior.revision),
                        digest=prior_digest,
                    ),
                    role=ContextEntryRole.EVIDENCE,
                    selection_reason="completed predecessor Run output for current Step",
                    inline_content=prior_content,
                    content_digest=prior_digest,
                    trust=ContextTrust.TRUSTED,
                    data_classification=ContextDataClassification.INTERNAL,
                    priority=70,
                    relevance=0.9,
                    project_id=request.project_id,
                )
            )
        return tuple(candidates)

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

        try:
            execution_binding = decode_agent_step_execution_binding(
                task.task.metadata,
                request.step_id,
            )
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                f"canonical kernel Step has invalid Agent execution metadata: {exc}",
            ) from exc

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

        content_payload: dict[str, object] = {
            "plan_id": request.plan_id,
            "plan_event_id": plan_event.id,
            "step_id": request.step_id,
            "step_title": title,
            "step_objective": objective,
            "dependencies": dependencies,
            "projection": "kernel_event",
        }
        if execution_binding is not None and execution_binding.objective is not None:
            content_payload["execution_objective"] = execution_binding.objective

        content = _canonical_json(content_payload)
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
    return sha256(content.encode("utf-8")).hexdigest()


__all__ = ["KernelFallbackPlanStepContextSourceAdapter"]
