"""Worker runtime adapters using canonical lifecycle contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ai_multi_agent_platform.contracts import ExecutionHandle, ExecutionSnapshot, LifecycleBackend
from ai_multi_agent_platform.domain import RunStatus

from .models import JobResultStatus, WorkerJobRequest, WorkerJobResult
from .registry import RegistryError


class WorkerDispatcher(Protocol):
    """Transport-neutral worker job operations consumed by the scheduler runtime."""

    @property
    def worker_id(self) -> str: ...

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle: ...

    async def get(self, worker_job_id: str) -> ExecutionSnapshot: ...

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot: ...


@dataclass(frozen=True, slots=True)
class _AcceptedJob:
    request: WorkerJobRequest
    handle: ExecutionHandle


class LocalWorker:
    """Reference worker proving local execution uses the same remote-job contract."""

    def __init__(self, worker_id: str, lifecycle: LifecycleBackend) -> None:
        self._worker_id = worker_id
        self._lifecycle = lifecycle
        self._jobs: dict[str, _AcceptedJob] = {}

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        existing = self._jobs.get(job.worker_job_id)
        if existing is not None:
            if existing.request != job:
                raise RegistryError("duplicate worker_job_id carries a different request")
            return existing.handle
        handle = await self._lifecycle.start(job.execution)
        self._jobs[job.worker_job_id] = _AcceptedJob(request=job, handle=handle)
        return handle

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        accepted = self._accepted(worker_job_id)
        return await self._lifecycle.get(
            accepted.request.execution.run_id,
            accepted.request.execution.context,
        )

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        accepted = self._accepted(worker_job_id)
        return await self._lifecycle.cancel(
            accepted.request.execution.run_id,
            accepted.request.execution.context,
        )

    async def result(self, worker_job_id: str) -> WorkerJobResult | None:
        snapshot = await self.get(worker_job_id)
        status = _terminal_result_status(snapshot.status)
        if status is None:
            return None
        accepted = self._accepted(worker_job_id)
        return WorkerJobResult(
            worker_job_id=worker_job_id,
            worker_id=self.worker_id,
            status=status,
            execution=snapshot,
            artifact_refs=accepted.request.artifact_refs,
        )

    def _accepted(self, worker_job_id: str) -> _AcceptedJob:
        try:
            return self._jobs[worker_job_id]
        except KeyError as exc:
            raise RegistryError(f"worker job is unknown locally: {worker_job_id}") from exc


def _terminal_result_status(status: RunStatus) -> JobResultStatus | None:
    return {
        RunStatus.SUCCEEDED: JobResultStatus.SUCCEEDED,
        RunStatus.FAILED: JobResultStatus.FAILED,
        RunStatus.CANCELLED: JobResultStatus.CANCELLED,
        RunStatus.TIMED_OUT: JobResultStatus.TIMED_OUT,
    }.get(status)
