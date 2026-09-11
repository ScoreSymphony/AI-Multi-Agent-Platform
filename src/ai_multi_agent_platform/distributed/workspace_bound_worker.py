"""Worker job routing bound to already-materialized Worker-local Workspaces."""

from __future__ import annotations

from typing import Protocol, cast

from ai_multi_agent_platform.contracts import ExecutionHandle, ExecutionSnapshot, LifecycleBackend

from .models import JobResultStatus, WorkerJobRequest, WorkerJobResult
from .registry import RegistryError
from .worker import LocalWorker, WorkerDispatcher
from .workspace_materialization_store import WorkerWorkspaceMaterializationStore


class WorkspaceLifecycleFactory(Protocol):
    """Build a lifecycle backend bound to one Worker-local execution workspace token."""

    def __call__(self, execution_workspace: str) -> LifecycleBackend: ...


class WorkspaceBoundLocalWorker:
    """Bind canonical Worker jobs to an already-materialized Worker-local Workspace.

    Jobs without Workspace references can delegate to an ordinary fallback dispatcher.
    Workspace jobs receive a lifecycle backend built for the exact local execution token
    resolved from the Worker materialization store.
    """

    def __init__(
        self,
        worker_id: str,
        store: WorkerWorkspaceMaterializationStore,
        lifecycle_factory: WorkspaceLifecycleFactory,
        *,
        fallback: WorkerDispatcher | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if worker_id != store.worker_id:
            raise ValueError("Workspace store belongs to a different Worker")
        self._worker_id = worker_id
        self._store = store
        self._lifecycle_factory = lifecycle_factory
        self._fallback = fallback
        self._routes: dict[str, WorkerDispatcher] = {}
        self._requests: dict[str, WorkerJobRequest] = {}

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        existing = self._requests.get(job.worker_job_id)
        if existing is not None:
            if existing != job:
                raise RegistryError("duplicate worker_job_id carries a different Workspace job")
            return await self._routes[job.worker_job_id].dispatch(job)
        if job.workspace_ref is None and job.snapshot_ref is None:
            if self._fallback is None:
                raise RegistryError("Worker job has no materialized Workspace and no fallback")
            route = self._fallback
        elif job.workspace_ref is None or job.snapshot_ref is None:
            raise RegistryError("Workspace Worker jobs require both workspace_ref and snapshot_ref")
        else:
            execution_workspace = self._store.execution_workspace(
                job.workspace_ref,
                job.snapshot_ref,
            )
            route = LocalWorker(
                self.worker_id,
                self._lifecycle_factory(execution_workspace),
            )
        handle = await route.dispatch(job)
        self._routes[job.worker_job_id] = route
        self._requests[job.worker_job_id] = job
        return handle

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        return await self._route(worker_job_id).get(worker_job_id)

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        return await self._route(worker_job_id).cancel(worker_job_id)

    async def result(self, worker_job_id: str) -> WorkerJobResult | None:
        route = self._route(worker_job_id)
        if isinstance(route, LocalWorker):
            return await route.result(worker_job_id)
        result_method = getattr(route, "result", None)
        if result_method is None:
            snapshot = await route.get(worker_job_id)
            status = _terminal_result_status(snapshot.status.value)
            if status is None:
                return None
            return WorkerJobResult(
                worker_job_id=worker_job_id,
                worker_id=self.worker_id,
                status=status,
                execution=snapshot,
            )
        result = await result_method(worker_job_id)
        return cast(WorkerJobResult | None, result)

    def _route(self, worker_job_id: str) -> WorkerDispatcher:
        try:
            return self._routes[worker_job_id]
        except KeyError as exc:
            raise RegistryError(f"Worker job is unknown locally: {worker_job_id}") from exc


def _terminal_result_status(status: str) -> JobResultStatus | None:
    return {
        "succeeded": JobResultStatus.SUCCEEDED,
        "failed": JobResultStatus.FAILED,
        "cancelled": JobResultStatus.CANCELLED,
        "timed_out": JobResultStatus.TIMED_OUT,
    }.get(status)


__all__ = ["WorkspaceBoundLocalWorker", "WorkspaceLifecycleFactory"]
