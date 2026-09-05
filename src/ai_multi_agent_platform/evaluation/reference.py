"""Reference evaluation execution through the canonical PlatformKernel."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Literal, cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.kernel import TERMINAL_RUN_STATUSES, PlatformKernel
from ai_multi_agent_platform.workspaces import (
    RunWorkspaceBinding,
    RunWorkspaceBindingRepository,
)

from .context import EvaluationExecutionContext
from .models import EvaluationAttempt, EvaluationCase, EvaluationObservation

OwnerType = Literal["user", "organization", "team", "service"]
TaskMetadataFactory = Callable[
    [EvaluationCase, EvaluationExecutionContext],
    Mapping[str, JsonValue],
]


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
        run_workspace_bindings: RunWorkspaceBindingRepository | None = None,
        task_metadata_factory: TaskMetadataFactory | None = None,
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
        self._run_workspace_bindings = run_workspace_bindings
        self._task_metadata_factory = task_metadata_factory

    def _project_for(self, execution_context: EvaluationExecutionContext) -> str | None:
        if execution_context.owner_type is not None:
            if execution_context.owner_type != self._owner_type:
                raise ValueError("evaluation workspace owner_type does not match executor owner")
            if execution_context.owner_id != self._owner_id:
                raise ValueError("evaluation workspace owner_id does not match executor owner")
        if (
            self._project_id is not None
            and execution_context.project_id is not None
            and self._project_id != execution_context.project_id
        ):
            raise ValueError("evaluation workspace project does not match executor project")
        return execution_context.project_id or self._project_id

    async def _bind_workspace(
        self,
        *,
        task_id: str,
        run_id: str,
        execution_context: EvaluationExecutionContext,
    ) -> None:
        if not execution_context.has_workspace:
            return
        if self._run_workspace_bindings is None:
            raise ValueError(
                "workspace-backed kernel evaluation requires RunWorkspaceBindingRepository"
            )
        if execution_context.project_id is None:
            raise ValueError("workspace-backed kernel evaluation requires project_id")
        assert execution_context.workspace_id is not None
        assert execution_context.workspace_snapshot_id is not None
        assert execution_context.workspace_content_checksum is not None
        await self._run_workspace_bindings.bind(
            RunWorkspaceBinding(
                run_id=run_id,
                task_id=task_id,
                workspace_id=execution_context.workspace_id,
                workspace_snapshot_id=execution_context.workspace_snapshot_id,
                content_checksum=execution_context.workspace_content_checksum,
            )
        )

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        if execution_context.attempt_id != attempt.attempt_id:
            raise ValueError("evaluation execution context belongs to another attempt")
        if execution_context.has_workspace and self._run_workspace_bindings is None:
            raise ValueError(
                "workspace-backed kernel evaluation requires RunWorkspaceBindingRepository"
            )

        title = _input_string(case, "title", f"Evaluation: {case.name}")
        objective = _input_string(case, "objective", case.name)
        key = attempt.attempt_id
        project_id = self._project_for(execution_context)

        task = await self._kernel.create_task(
            idempotency_key=f"{key}:create-task",
            title=title,
            objective=objective,
            owner_type=self._owner_type,
            owner_id=self._owner_id,
            project_id=project_id,
            actor_ref=self._actor_ref,
            source=self._source,
        )
        if self._task_metadata_factory is not None:
            metadata = dict(self._task_metadata_factory(case, execution_context))
            if metadata:
                task = await self._kernel.update_task(
                    idempotency_key=f"{key}:configure-task",
                    task_id=task.task_id,
                    metadata=metadata,
                    actor_ref=self._actor_ref,
                    source=self._source,
                )
        await self._kernel.ready_task(
            idempotency_key=f"{key}:ready-task",
            task_id=task.task_id,
            actor_ref=self._actor_ref,
            source=self._source,
        )
        task = await self._kernel.get_task(task.task_id)
        if task.plan_ref is None:
            await self._kernel.plan_task(
                idempotency_key=f"{key}:plan-task",
                task_id=task.task_id,
                actor_ref=self._actor_ref,
                source=self._source,
            )
        run = await self._kernel.create_run(
            idempotency_key=f"{key}:create-run",
            task_id=task.task_id,
            actor_ref=self._actor_ref,
            source=self._source,
        )
        await self._bind_workspace(
            task_id=task.task_id,
            run_id=run.run_id,
            execution_context=execution_context,
        )
        run = await self._kernel.start_run(
            idempotency_key=f"{key}:start-run",
            task_id=task.task_id,
            run_id=run.run_id,
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

        event_evidence: list[JsonValue] = []
        for event in events:
            event_evidence.append(
                {
                    "id": event.id,
                    "event_type": event.event_type,
                    "subject_type": event.subject_type,
                    "subject_id": event.subject_id,
                    "correlation_id": event.correlation_id,
                    "causation_id": event.causation_id,
                    "payload": cast(dict[str, JsonValue], dict(event.payload)),
                }
            )

        data: dict[str, JsonValue] = {
            "input": case.input_template,
            "task": {
                "id": task.task_id,
                "status": task.status.value,
                "revision": task.revision,
                "plan_ref": task.plan_ref,
                "step_ids": list(task.step_ids),
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
            "events": event_evidence,
        }
        if execution_context.has_workspace:
            data["workspace"] = {
                "workspace_id": execution_context.workspace_id,
                "workspace_snapshot_id": execution_context.workspace_snapshot_id,
                "content_checksum": execution_context.workspace_content_checksum,
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


__all__ = ["KernelEvaluationCaseExecutor", "OwnerType", "TaskMetadataFactory"]
