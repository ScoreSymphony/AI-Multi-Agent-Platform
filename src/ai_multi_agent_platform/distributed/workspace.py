"""#37 remote workspace composition for the transport-neutral Worker dispatcher."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from ai_multi_agent_platform.contracts import ExecutionHandle, ExecutionSnapshot
from ai_multi_agent_platform.domain import RunStatus
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    RemoteCleanupAcknowledgement,
    RemoteMaterializationReceipt,
    RemoteMaterializationRequest,
    RemoteMaterializationResult,
    RemoteWorkspaceMaterializer,
    WorkspaceProvider,
)

from .models import JobResultStatus, WorkerJobRequest, WorkerJobResult
from .registry import RegistryError
from .worker import WorkerDispatcher


class WorkerWorkspaceResolver(Protocol):
    """Resolve persisted canonical workspace/snapshot refs into the existing #37 request."""

    async def resolve(self, job: WorkerJobRequest) -> RemoteMaterializationRequest | None: ...


class WorkspaceJobMaterializationResolver:
    """Build #37 remote requests from canonical workspace state without host paths."""

    def __init__(self, provider: WorkspaceProvider) -> None:
        self.provider = provider

    async def resolve(self, job: WorkerJobRequest) -> RemoteMaterializationRequest | None:
        if job.workspace_ref is None and job.snapshot_ref is None:
            return None
        if job.workspace_ref is None or job.snapshot_ref is None:
            raise RegistryError("remote workspace jobs require both workspace_ref and snapshot_ref")
        workspace = await self.provider.get_workspace(job.workspace_ref)
        snapshot = await self.provider.get_snapshot(job.snapshot_ref)
        if snapshot.workspace_id != workspace.id:
            raise RegistryError("worker job snapshot does not belong to its workspace")
        return RemoteMaterializationRequest(
            workspace_id=workspace.id,
            snapshot_id=snapshot.id,
            expected_checksum=snapshot.content_checksum,
            access_mode=workspace.access_mode,
            cache_key=f"{snapshot.id}:{snapshot.content_checksum}",
        )


@dataclass(frozen=True, slots=True)
class WorkspaceDispatchEvidence:
    worker_job_id: str
    receipt: RemoteMaterializationReceipt | None = None
    result: RemoteMaterializationResult | None = None
    cleanup: RemoteCleanupAcknowledgement | None = None


@dataclass(frozen=True, slots=True)
class _WorkspaceDispatchState:
    request: WorkerJobRequest
    materialization_request: RemoteMaterializationRequest | None = None
    receipt: RemoteMaterializationReceipt | None = None
    handle: ExecutionHandle | None = None
    result: RemoteMaterializationResult | None = None
    cleanup: RemoteCleanupAcknowledgement | None = None


class MaterializingWorkerDispatcher:
    """Materialize #37 workspace state before dispatch and finalize it at terminal state.

    The materializer and wrapped dispatcher are deployment adapters bound to the same
    canonical Worker. The wrapper never exposes or constructs a host-local path; it
    carries only the opaque receipt/result references defined by #37.
    """

    def __init__(
        self,
        dispatcher: WorkerDispatcher,
        materializer: RemoteWorkspaceMaterializer,
        resolver: WorkerWorkspaceResolver,
    ) -> None:
        self._dispatcher = dispatcher
        self._materializer = materializer
        self._resolver = resolver
        self._jobs: dict[str, _WorkspaceDispatchState] = {}

    @property
    def worker_id(self) -> str:
        return self._dispatcher.worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        existing = self._jobs.get(job.worker_job_id)
        if existing is not None:
            if existing.request != job:
                raise RegistryError("duplicate worker_job_id carries a different workspace request")
            if existing.handle is not None:
                return existing.handle
            handle = await self._dispatcher.dispatch(job)
            self._jobs[job.worker_job_id] = replace(existing, handle=handle)
            return handle

        materialization_request = await self._resolver.resolve(job)
        receipt = None
        if materialization_request is not None:
            receipt = await self._materializer.materialize(materialization_request)
            self._validate_receipt(materialization_request, receipt)
        state = _WorkspaceDispatchState(
            request=job,
            materialization_request=materialization_request,
            receipt=receipt,
        )
        self._jobs[job.worker_job_id] = state
        handle = await self._dispatcher.dispatch(job)
        self._jobs[job.worker_job_id] = replace(state, handle=handle)
        return handle

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        self._state(worker_job_id)
        snapshot = await self._dispatcher.get(worker_job_id)
        if _terminal_status(snapshot.status) is not None:
            await self._finalize(worker_job_id, snapshot.status)
        return snapshot

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        self._state(worker_job_id)
        snapshot = await self._dispatcher.cancel(worker_job_id)
        if _terminal_status(snapshot.status) is not None:
            await self._finalize(worker_job_id, snapshot.status)
        return snapshot

    async def result(self, worker_job_id: str) -> WorkerJobResult | None:
        snapshot = await self.get(worker_job_id)
        status = _terminal_status(snapshot.status)
        if status is None:
            return None
        state = self._state(worker_job_id)
        artifact_refs = list(state.request.artifact_refs)
        if state.result is not None:
            artifact_refs.extend(state.result.artifact_ids)
        cleanup_error = None
        if state.cleanup is not None and not state.cleanup.succeeded:
            cleanup_error = state.cleanup.error_code
        return WorkerJobResult(
            worker_job_id=worker_job_id,
            worker_id=self.worker_id,
            status=status,
            execution=snapshot,
            artifact_refs=tuple(dict.fromkeys(artifact_refs)),
            error_category=cleanup_error,
        )

    def evidence(self, worker_job_id: str) -> WorkspaceDispatchEvidence:
        state = self._state(worker_job_id)
        return WorkspaceDispatchEvidence(
            worker_job_id=worker_job_id,
            receipt=state.receipt,
            result=state.result,
            cleanup=state.cleanup,
        )

    async def _finalize(self, worker_job_id: str, status: RunStatus) -> None:
        state = self._state(worker_job_id)
        receipt = state.receipt
        if receipt is None or state.cleanup is not None:
            return
        result = state.result
        if result is None:
            result = await self._materializer.collect_result(receipt)
            self._validate_result(receipt, result)
            state = replace(state, result=result)
            self._jobs[worker_job_id] = state
        cleanup = await self._materializer.cleanup(receipt, _materialization_outcome(status))
        self._validate_cleanup(receipt, cleanup)
        self._jobs[worker_job_id] = replace(state, cleanup=cleanup)

    def _state(self, worker_job_id: str) -> _WorkspaceDispatchState:
        try:
            return self._jobs[worker_job_id]
        except KeyError as exc:
            raise RegistryError(
                f"worker job is unknown to workspace dispatcher: {worker_job_id}"
            ) from exc

    def _validate_receipt(
        self,
        request: RemoteMaterializationRequest,
        receipt: RemoteMaterializationReceipt,
    ) -> None:
        if receipt.worker_ref != self.worker_id:
            raise RegistryError("remote materialization receipt belongs to a different Worker")
        if (
            receipt.workspace_id != request.workspace_id
            or receipt.snapshot_id != request.snapshot_id
        ):
            raise RegistryError("remote materialization receipt identity mismatch")
        if receipt.expected_checksum != request.expected_checksum:
            raise RegistryError("remote materialization receipt checksum identity mismatch")
        if receipt.access_mode is not request.access_mode:
            raise RegistryError("remote materialization receipt access mode mismatch")

    @staticmethod
    def _validate_result(
        receipt: RemoteMaterializationReceipt,
        result: RemoteMaterializationResult,
    ) -> None:
        if result.workspace_id != receipt.workspace_id or result.snapshot_id != receipt.snapshot_id:
            raise RegistryError("remote materialization result identity mismatch")
        if result.materialization_ref != receipt.materialization_ref:
            raise RegistryError("remote materialization result reference mismatch")

    @staticmethod
    def _validate_cleanup(
        receipt: RemoteMaterializationReceipt,
        cleanup: RemoteCleanupAcknowledgement,
    ) -> None:
        if (
            cleanup.workspace_id != receipt.workspace_id
            or cleanup.snapshot_id != receipt.snapshot_id
        ):
            raise RegistryError("remote cleanup acknowledgement identity mismatch")
        if cleanup.materialization_ref != receipt.materialization_ref:
            raise RegistryError("remote cleanup acknowledgement reference mismatch")


def _terminal_status(status: RunStatus) -> JobResultStatus | None:
    return {
        RunStatus.SUCCEEDED: JobResultStatus.SUCCEEDED,
        RunStatus.FAILED: JobResultStatus.FAILED,
        RunStatus.CANCELLED: JobResultStatus.CANCELLED,
        RunStatus.TIMED_OUT: JobResultStatus.TIMED_OUT,
    }.get(status)


def _materialization_outcome(status: RunStatus) -> MaterializationOutcome:
    if status is RunStatus.SUCCEEDED:
        return MaterializationOutcome.SUCCEEDED
    if status is RunStatus.CANCELLED:
        return MaterializationOutcome.CANCELLED
    return MaterializationOutcome.FAILED
