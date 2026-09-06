"""Lifecycle adapter routing canonical Run execution through ``DistributedRuntime``.

The platform kernel continues to own Task/Run lifecycle truth. This adapter only translates the
existing provider-neutral ``LifecycleBackend`` seam into the canonical #14 Worker Job scheduler
and dispatch path, so a normal Run can execute locally or remotely without Task-specific logic.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    ErrorCode,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    HealthStatus,
    LifecycleBackend,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.workspaces import RunWorkspaceBindingRepository

from .models import JobRequirements, WorkerJobRequest
from .registry import RegistryError
from .runtime import DispatchAuthorizationError, DispatchRecord, DispatchState, DistributedRuntime
from .scheduler import NoEligibleWorkerError

_METADATA_NAMESPACE = "distributed-lifecycle"


class DistributedLifecycleBackend(LifecycleBackend):
    """Execute canonical Runs through one canonical distributed Worker runtime."""

    def __init__(
        self,
        runtime: DistributedRuntime,
        *,
        requirements: JobRequirements | None = None,
        workspace_bindings: RunWorkspaceBindingRepository | None = None,
    ) -> None:
        self.runtime = runtime
        self.requirements = requirements or JobRequirements()
        self.workspace_bindings = workspace_bindings

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="distributed-lifecycle",
            provider_type="execution",
            supported_operations=("start", "get", "cancel"),
            health=HealthStatus.HEALTHY,
            available=True,
            resources={"scheduler": "distributed-runtime"},
        )

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        binding = (
            None
            if self.workspace_bindings is None
            else await self.workspace_bindings.get(request.run_id)
        )
        job = WorkerJobRequest(
            worker_job_id=_worker_job_id(request.run_id),
            execution=request,
            requirements=self.requirements,
            workspace_ref=None if binding is None else binding.workspace_id,
            snapshot_ref=None if binding is None else binding.workspace_snapshot_id,
            timeout_seconds=request.context.control.timeout_seconds,
            idempotency_key=request.context.control.idempotency_key,
        )
        try:
            record = await self.runtime.dispatch(job)
        except NoEligibleWorkerError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                str(exc),
                retryable=True,
                provider_id=self.descriptor.provider_id,
            ) from exc
        except DispatchAuthorizationError as exc:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                str(exc),
                provider_id=self.descriptor.provider_id,
            ) from exc
        except ContractError:
            raise
        except RegistryError as exc:
            raise _registry_error(exc, provider_id=self.descriptor.provider_id) from exc

        if record.handle is None:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "distributed dispatch returned no execution handle",
                provider_id=self.descriptor.provider_id,
            )
        return _handle(record)

    async def get(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        del context
        worker_job_id = _worker_job_id(run_id)
        try:
            await self.runtime.reconcile()
            record = self.runtime.get_record(worker_job_id)
        except RegistryError as exc:
            raise _registry_error(
                exc,
                provider_id=self.descriptor.provider_id,
                unknown_is_not_found=True,
            ) from exc

        if record.snapshot is not None:
            return _snapshot(record, record.snapshot)
        if record.state in {DispatchState.LOST, DispatchState.CANCEL_PENDING}:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"distributed execution is not currently reachable: {run_id}",
                retryable=True,
                provider_id=self.descriptor.provider_id,
            )
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            f"distributed execution has no observable snapshot: {run_id}",
            provider_id=self.descriptor.provider_id,
        )

    async def cancel(self, run_id: str, context: OperationContext) -> ExecutionSnapshot:
        del context
        worker_job_id = _worker_job_id(run_id)
        try:
            record = await self.runtime.cancel(worker_job_id)
        except RegistryError as exc:
            raise _registry_error(
                exc,
                provider_id=self.descriptor.provider_id,
                unknown_is_not_found=True,
            ) from exc
        if record.state is DispatchState.CANCEL_PENDING or record.snapshot is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"distributed cancellation is pending Worker reconciliation: {run_id}",
                retryable=True,
                provider_id=self.descriptor.provider_id,
            )
        return _snapshot(record, record.snapshot)


def _worker_job_id(run_id: str) -> str:
    value = uuid5(NAMESPACE_URL, f"ai-multi-agent-platform:distributed-lifecycle:{run_id}")
    return f"worker_job_{value}"


def _metadata(record: DispatchRecord) -> AdapterMetadata:
    return AdapterMetadata(
        namespace=_METADATA_NAMESPACE,
        values={
            "worker_job_id": record.job.worker_job_id,
            "worker_id": record.worker_id,
            "dispatch_state": record.state.value,
        },
    )


def _handle(record: DispatchRecord) -> ExecutionHandle:
    assert record.handle is not None
    return replace(
        record.handle,
        adapter_metadata=(*record.handle.adapter_metadata, _metadata(record)),
    )


def _snapshot(record: DispatchRecord, snapshot: ExecutionSnapshot) -> ExecutionSnapshot:
    return replace(
        snapshot,
        adapter_metadata=(*snapshot.adapter_metadata, _metadata(record)),
    )


def _registry_error(
    exc: RegistryError,
    *,
    provider_id: str,
    unknown_is_not_found: bool = False,
) -> ContractError:
    message = str(exc)
    if unknown_is_not_found and "unknown dispatched worker job" in message:
        return ContractError(ErrorCode.NOT_FOUND, message, provider_id=provider_id)
    if "no attached dispatcher" in message or "not currently reachable" in message:
        return ContractError(
            ErrorCode.UNAVAILABLE,
            message,
            retryable=True,
            provider_id=provider_id,
        )
    return ContractError(ErrorCode.CONFLICT, message, provider_id=provider_id)


__all__ = ["DistributedLifecycleBackend"]
