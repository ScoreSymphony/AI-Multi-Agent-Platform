"""Execute canonical Worker Jobs through the platform-owned Executor seam.

Unlike ``LocalWorker``, this dispatcher does not route sub-work through a Run
``LifecycleBackend``. The canonical Run remains the parent lifecycle identity while
``worker_job_id`` owns the subordinate dispatch identity, so multiple executor jobs may
belong to one Run without sharing a lifecycle result cache.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ExecutionHandle,
    ExecutionSnapshot,
    JsonValue,
)
from ai_multi_agent_platform.execution import (
    ExecutionRequest as ExecutorExecutionRequest,
)
from ai_multi_agent_platform.execution import ExecutionResult, Executor

from .models import JobResultStatus, WorkerJobRequest, WorkerJobResult
from .registry import RegistryError

EXECUTOR_WORKER_INPUT_KEY = "executor_worker"
EXECUTOR_WORKER_INPUT_SCHEMA = "ai-multi-agent-platform/executor-worker/v1"

type ExecutorWorkspaceResolver = Callable[[WorkerJobRequest], str]


def executor_worker_input(
    *,
    action: str,
    arguments: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    """Encode one executor operation inside the existing transport-neutral job input."""

    if not action.strip():
        raise ValueError("executor worker action must not be blank")
    return {
        EXECUTOR_WORKER_INPUT_KEY: {
            "schema": EXECUTOR_WORKER_INPUT_SCHEMA,
            "action": action,
            "arguments": dict(arguments),
        }
    }


@dataclass(frozen=True, slots=True)
class _AcceptedExecution:
    request: WorkerJobRequest
    result: ExecutionResult


class ExecutorWorker:
    """Worker dispatcher whose idempotency/state boundary is ``worker_job_id``."""

    def __init__(
        self,
        worker_id: str,
        executor: Executor,
        *,
        workspace: str,
        workspace_resolver: ExecutorWorkspaceResolver | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if not workspace.strip():
            raise ValueError("executor worker workspace must not be blank")
        self._worker_id = worker_id
        self._executor = executor
        self._workspace = workspace
        self._workspace_resolver = workspace_resolver
        self._jobs: dict[str, _AcceptedExecution] = {}

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        existing = self._jobs.get(job.worker_job_id)
        if existing is not None:
            if existing.request != job:
                raise RegistryError("duplicate worker_job_id carries a different executor request")
            return self._handle(job, existing.result)

        if job.execution.subject_type != "task":
            raise RegistryError("executor Worker currently requires a canonical Task subject")
        action, arguments = _decode_executor_input(job.execution.input)
        workspace = self._workspace
        if self._workspace_resolver is not None:
            workspace = self._workspace_resolver(job)
            if not workspace.strip():
                raise RegistryError("executor Worker workspace resolver returned a blank token")

        result = await self._executor.execute(
            ExecutorExecutionRequest(
                task_id=job.execution.subject_id,
                run_id=job.execution.run_id,
                correlation_id=job.execution.context.correlation_id,
                action=action,
                workspace=workspace,
                arguments=arguments,
                timeout_seconds=job.timeout_seconds
                or job.execution.context.control.timeout_seconds,
            )
        )
        self._jobs[job.worker_job_id] = _AcceptedExecution(request=job, result=result)
        return self._handle(job, result)

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        accepted = self._accepted(worker_job_id)
        return self._snapshot(accepted.request, accepted.result)

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        # Reference executors complete within ``dispatch``. A future asynchronous executor
        # adapter can own cancellable handles without changing the Worker Job identity model.
        return await self.get(worker_job_id)

    async def result(self, worker_job_id: str) -> WorkerJobResult | None:
        accepted = self._accepted(worker_job_id)
        status = _job_status(accepted.result.status.value)
        if status is None:
            return None
        return WorkerJobResult(
            worker_job_id=worker_job_id,
            worker_id=self.worker_id,
            status=status,
            execution=self._snapshot(accepted.request, accepted.result),
            artifact_refs=accepted.request.artifact_refs,
            error_category=(
                None if accepted.result.error is None else accepted.result.error.category.value
            ),
        )

    def _accepted(self, worker_job_id: str) -> _AcceptedExecution:
        try:
            return self._jobs[worker_job_id]
        except KeyError as exc:
            raise RegistryError(f"executor Worker job is unknown locally: {worker_job_id}") from exc

    def _handle(self, job: WorkerJobRequest, result: ExecutionResult) -> ExecutionHandle:
        return ExecutionHandle(
            run_id=job.execution.run_id,
            backend_ref=(
                f"executor-worker:{self.worker_id}:{self._executor.descriptor.executor_id}:"
                f"{job.worker_job_id}"
            ),
            adapter_metadata=self._metadata(job, result),
        )

    def _snapshot(self, job: WorkerJobRequest, result: ExecutionResult) -> ExecutionSnapshot:
        return ExecutionSnapshot(
            run_id=job.execution.run_id,
            status=result.status,
            output=dict(result.output),
            adapter_metadata=self._metadata(job, result),
        )

    def _metadata(
        self,
        job: WorkerJobRequest,
        result: ExecutionResult,
    ) -> tuple[AdapterMetadata, ...]:
        return (
            AdapterMetadata(
                namespace="executor-worker",
                values={
                    "worker_job_id": job.worker_job_id,
                    "worker_id": self.worker_id,
                    "executor_id": self._executor.descriptor.executor_id,
                    "result_code": result.result_code,
                },
            ),
        )


def _decode_executor_input(
    value: Mapping[str, JsonValue],
) -> tuple[str, dict[str, JsonValue]]:
    raw = value.get(EXECUTOR_WORKER_INPUT_KEY)
    if not isinstance(raw, dict):
        raise RegistryError("Worker Job is missing the executor operation payload")
    if raw.get("schema") != EXECUTOR_WORKER_INPUT_SCHEMA:
        raise RegistryError("Worker Job executor operation schema is unsupported")
    action = raw.get("action")
    arguments = raw.get("arguments")
    if not isinstance(action, str) or not action.strip():
        raise RegistryError("Worker Job executor action is invalid")
    if not isinstance(arguments, dict):
        raise RegistryError("Worker Job executor arguments are invalid")
    return action, cast(dict[str, JsonValue], dict(arguments))


def _job_status(value: str) -> JobResultStatus | None:
    return {
        "succeeded": JobResultStatus.SUCCEEDED,
        "failed": JobResultStatus.FAILED,
        "cancelled": JobResultStatus.CANCELLED,
        "timed_out": JobResultStatus.TIMED_OUT,
    }.get(value)


__all__ = [
    "EXECUTOR_WORKER_INPUT_KEY",
    "EXECUTOR_WORKER_INPUT_SCHEMA",
    "ExecutorWorker",
    "ExecutorWorkspaceResolver",
    "executor_worker_input",
]
