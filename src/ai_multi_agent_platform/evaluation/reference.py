"""Reference evaluation execution through the canonical PlatformKernel."""

from __future__ import annotations

import asyncio
from typing import Literal

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.kernel import PlatformKernel, TERMINAL_RUN_STATUSES

from .models import EvaluationAttempt, EvaluationCase, EvaluationObservation

OwnerType = Literal["user", "organization", "team", "service"]


def _input_string(case: EvaluationCase, key: str, default: str) -> str:
    value = case.input_template.get(key)
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"evaluation case input '{key}' must be a non-blank string")
    return value


class KernelEvaluationCaseExecutor:
    """Execute cases through canonical Task/Run lifecycle and project resulting evidence."""

    def __init__(
        self,
        *,
        kernel: PlatformKernel,
        owner_type: OwnerType,
        owner_id: str,
        project_id: str | None = None,
        actor_ref: str | None = None,
        source: str = "evaluation-reference",
        poll_interval_seconds: float = 0.01,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("evaluation executor owner_id must not be blank")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than zero")
        self._kernel = kernel
        self._owner_type = owner_type
        self._owner_id = owner_id
        self._project_id = project_id
        self._actor_ref = actor_ref
        self._source = source
        self._poll_interval_seconds = poll_interval_seconds

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
    ) -> EvaluationObservation:
        title = _input_string(case, "title", f"Evaluation: {case.name}")
        objective = _input_string(case, "objective", case.name)
        key = attempt.attempt_id

        task = await self._kernel.create_task(
            idempotency_key=f"{key}:create-task",
            title=title,
            objective=objective,
            owner_type=self._owner_type,
            owner_id=self._owner_id,
            project_id=self._project_id,
            actor_ref=self._actor_ref,
            source=self._source,
        )
        await self._kernel.ready_task(
            idempotency_key=f"{key}:ready-task",
            task_id=task.task_id,
            actor_ref=self._actor_ref,
            source=self._source,
        )
        run = await self._kernel.start_task(
            idempotency_key=f"{key}:start-task",
            task_id=task.task_id,
            actor_ref=self._actor_ref,
            source=self._source,
        )

        refresh_index = 0
        while run.status not in TERMINAL_RUN_STATUSES:
            await asyncio.sleep(self._poll_interval_seconds)
            refresh_index += 1
            run = await self._kernel.refresh_run(
                idempotency_key=f"{key}:refresh:{refresh_index}",
                task_id=task.task_id,
                run_id=run.run_id,
                actor_ref=self._actor_ref,
                source=self._source,
            )

        task = await self._kernel.get_task(task.task_id)
        events = await self._kernel.history(task.task_id)
        artifact_refs = tuple(dict.fromkeys((*task.artifact_ids, *run.artifact_ids)))
        result_refs = tuple(dict.fromkeys((*task.result_ids, *run.result_ids)))

        data: dict[str, JsonValue] = {
            "input": case.input_template,
            "task": {
                "id": task.task_id,
                "status": task.status.value,
                "revision": task.revision,
            },
            "run": {
                "id": run.run_id,
                "status": run.status.value,
                "attempt": run.attempt,
                "revision": run.revision,
                "dispatch_attempts": run.dispatch_attempts,
                "output": run.output,
                "artifact_refs": list(run.artifact_ids),
                "result_refs": list(run.result_ids),
            },
            "artifact_refs": list(artifact_refs),
            "result_refs": list(result_refs),
            "event_types": [event.event_type for event in events],
        }
        metrics = {
            "event_count": float(len(events)),
            "dispatch_attempts": float(run.dispatch_attempts),
            "run_attempt": float(run.attempt),
        }
        return EvaluationObservation(
            data=data,
            metrics=metrics,
            task_id=task.task_id,
            run_id=run.run_id,
            artifact_refs=artifact_refs,
            event_types=tuple(event.event_type for event in events),
        )
