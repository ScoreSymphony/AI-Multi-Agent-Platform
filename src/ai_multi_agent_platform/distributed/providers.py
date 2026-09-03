"""#5 provider adapters backed by the platform-owned distributed runtime."""

from __future__ import annotations

from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import NodeProvider, WorkerProvider
from ai_multi_agent_platform.contracts.types import (
    AdapterMetadata,
    Capability,
    CapabilityKind,
    ExecutionHandle,
    ExecutionRequest,
    HealthStatus,
    JsonValue,
    NodeDescriptor,
    OperationContext,
    ProviderDescriptor,
    WorkerDescriptor,
)

from .models import NodeRecord, RegistrationRequest, WorkerJobRequest, WorkerRecord, WorkerStatus
from .registry import RegistryError
from .runtime import DistributedRuntime
from .scheduler import NoEligibleWorkerError

_PROVIDER_NAMESPACE = "distributed"


def _provider_descriptor(
    provider_id: str,
    provider_type: str,
    kind: CapabilityKind,
    operations: tuple[str, ...],
) -> ProviderDescriptor:
    capability = Capability(
        name=f"distributed.{provider_type}",
        kind=kind,
        supported_operations=operations,
        adapter_metadata=(
            AdapterMetadata(
                namespace=_PROVIDER_NAMESPACE,
                values={"implementation": "distributed-runtime"},
            ),
        ),
    )
    return ProviderDescriptor(
        provider_id=provider_id,
        provider_type=provider_type,
        supported_operations=operations,
        capabilities=(capability,),
        health=HealthStatus.HEALTHY,
        available=True,
        adapter_metadata=(
            AdapterMetadata(
                namespace=_PROVIDER_NAMESPACE,
                values={"implementation": "distributed-runtime"},
            ),
        ),
    )


class DistributedNodeProvider(NodeProvider):
    """Expose distributed Node runtime state through the replaceable #5 contract."""

    descriptor = _provider_descriptor(
        "distributed-nodes",
        "node",
        CapabilityKind.NODE,
        ("register_node", "list_nodes"),
    )

    def __init__(self, runtime: DistributedRuntime) -> None:
        self.runtime = runtime
        self._capabilities: dict[str, tuple[Capability, ...]] = {}

    async def register_node(
        self,
        node: NodeDescriptor,
        context: OperationContext,
    ) -> NodeDescriptor:
        del context
        existing = _optional_node(self.runtime, node.node_id)
        workers = tuple(
            worker
            for worker in self.runtime.registry.list_workers()
            if worker.node_id == node.node_id
        )
        record = _node_record(node, existing)
        try:
            registered = self.runtime.register(
                RegistrationRequest(node=record, workers=workers),
            )
        except RegistryError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"node registration rejected: {exc}",
                provider_id=self.descriptor.provider_id,
            ) from exc
        self._capabilities[node.node_id] = node.capabilities
        return _node_descriptor(
            registered,
            self._capabilities.get(node.node_id),
        )

    async def list_nodes(self, context: OperationContext) -> tuple[NodeDescriptor, ...]:
        del context
        return tuple(
            _node_descriptor(node, self._capabilities.get(node.node_id))
            for node in self.runtime.registry.list_nodes()
        )


class DistributedWorkerProvider(WorkerProvider):
    """Expose Worker discovery and exact-worker dispatch through the #5 contract."""

    descriptor = _provider_descriptor(
        "distributed-workers",
        "worker",
        CapabilityKind.WORKER,
        ("register_worker", "list_workers", "dispatch"),
    )

    def __init__(self, runtime: DistributedRuntime) -> None:
        self.runtime = runtime
        self._capabilities: dict[str, tuple[Capability, ...]] = {}

    async def register_worker(
        self,
        worker: WorkerDescriptor,
        context: OperationContext,
    ) -> WorkerDescriptor:
        del context
        try:
            node = self.runtime.registry.get_node(worker.node_id)
        except RegistryError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"worker node not found: {worker.node_id}",
                provider_id=self.descriptor.provider_id,
            ) from exc

        existing = _optional_worker(self.runtime, worker.worker_id)
        if existing is not None and existing.node_id != worker.node_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "worker registration cannot move a canonical Worker between Nodes",
                provider_id=self.descriptor.provider_id,
            )

        record = _worker_record(worker, existing)
        siblings = [
            current
            for current in self.runtime.registry.list_workers()
            if current.node_id == worker.node_id and current.worker_id != worker.worker_id
        ]
        workers = tuple(sorted((*siblings, record), key=lambda item: item.worker_id))
        try:
            self.runtime.register(RegistrationRequest(node=node, workers=workers))
        except RegistryError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"worker registration rejected: {exc}",
                provider_id=self.descriptor.provider_id,
            ) from exc
        self._capabilities[worker.worker_id] = worker.capabilities
        registered = self.runtime.registry.get_worker(worker.worker_id)
        return _worker_descriptor(
            registered,
            self._capabilities.get(worker.worker_id),
        )

    async def list_workers(self, context: OperationContext) -> tuple[WorkerDescriptor, ...]:
        del context
        return tuple(
            _worker_descriptor(worker, self._capabilities.get(worker.worker_id))
            for worker in self.runtime.registry.list_workers()
        )

    async def dispatch(self, worker_id: str, request: ExecutionRequest) -> ExecutionHandle:
        try:
            self.runtime.registry.get_worker(worker_id)
        except RegistryError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"worker not found: {worker_id}",
                provider_id=self.descriptor.provider_id,
            ) from exc

        job = WorkerJobRequest(
            worker_job_id=_provider_worker_job_id(worker_id, request.run_id),
            execution=request,
            timeout_seconds=request.context.control.timeout_seconds,
            idempotency_key=request.context.control.idempotency_key,
        )
        try:
            record = await self.runtime.dispatch_to_worker(job, worker_id)
        except NoEligibleWorkerError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                str(exc),
                retryable=True,
                provider_id=self.descriptor.provider_id,
            ) from exc
        except RegistryError as exc:
            message = str(exc)
            code = ErrorCode.CONFLICT
            retryable = False
            if "no attached dispatcher" in message:
                code = ErrorCode.UNAVAILABLE
                retryable = True
            raise ContractError(
                code,
                message,
                retryable=retryable,
                provider_id=self.descriptor.provider_id,
            ) from exc
        if record.handle is None:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "distributed dispatch completed without an execution handle",
                provider_id=self.descriptor.provider_id,
            )
        return record.handle


def _node_record(node: NodeDescriptor, existing: NodeRecord | None) -> NodeRecord:
    capability_refs = _capability_refs(node.capabilities)
    display_name = _metadata_string(node.metadata, "display_name")
    if existing is None:
        return NodeRecord(
            node_id=node.node_id,
            display_name=display_name or node.node_id,
            capability_refs=capability_refs,
            adapter_metadata=node.adapter_metadata,
        )
    return replace(
        existing,
        display_name=display_name or existing.display_name,
        capability_refs=capability_refs,
        adapter_metadata=_merge_adapter_metadata(existing.adapter_metadata, node.adapter_metadata),
    )


def _worker_record(
    worker: WorkerDescriptor,
    existing: WorkerRecord | None,
) -> WorkerRecord:
    capability_refs = _capability_refs(worker.capabilities)
    if existing is None:
        return WorkerRecord(
            worker_id=worker.worker_id,
            node_id=worker.node_id,
            capability_refs=capability_refs,
            status=WorkerStatus.HEALTHY if worker.available else WorkerStatus.OFFLINE,
            adapter_metadata=worker.adapter_metadata,
        )

    status = existing.status
    if not worker.available:
        status = WorkerStatus.OFFLINE
    elif status is WorkerStatus.OFFLINE:
        status = WorkerStatus.HEALTHY
    return replace(
        existing,
        capability_refs=capability_refs,
        status=status,
        adapter_metadata=_merge_adapter_metadata(existing.adapter_metadata, worker.adapter_metadata),
    )


def _node_descriptor(
    node: NodeRecord,
    capabilities: tuple[Capability, ...] | None,
) -> NodeDescriptor:
    return NodeDescriptor(
        node_id=node.node_id,
        capabilities=capabilities or _reported_capabilities(node.capability_refs, CapabilityKind.NODE),
        metadata={
            "display_name": node.display_name,
            "status": node.status.value,
            "labels": list(node.labels),
            "os_name": node.os_name,
            "platform": node.platform,
            "architecture": node.architecture,
            "supported_runtimes": list(node.supported_runtimes),
            "model_refs": list(node.model_refs),
            "worker_refs": list(node.worker_refs),
            "trust_level": node.trust_level,
            "draining": node.draining,
            "maintenance": node.maintenance,
            "network_available": node.network_available,
            "locality_refs": list(node.locality_refs),
            "resources": {
                "cpu_cores_total": node.resources.cpu_cores_total,
                "cpu_cores_available": node.resources.cpu_cores_available,
                "ram_total_bytes": node.resources.ram_total_bytes,
                "ram_available_bytes": node.resources.ram_available_bytes,
                "storage_total_bytes": node.resources.storage_total_bytes,
                "storage_available_bytes": node.resources.storage_available_bytes,
                "accelerator_count": len(node.resources.accelerators),
            },
        },
        adapter_metadata=node.adapter_metadata,
    )


def _worker_descriptor(
    worker: WorkerRecord,
    capabilities: tuple[Capability, ...] | None,
) -> WorkerDescriptor:
    return WorkerDescriptor(
        worker_id=worker.worker_id,
        node_id=worker.node_id,
        capabilities=capabilities
        or _reported_capabilities(worker.capability_refs, CapabilityKind.WORKER),
        available=worker.status is WorkerStatus.HEALTHY and not worker.draining,
        metadata={
            "worker_type": worker.worker_type,
            "supported_executors": list(worker.supported_executors),
            "supported_runtimes": list(worker.supported_runtimes),
            "model_refs": list(worker.model_refs),
            "concurrency_limit": worker.concurrency_limit,
            "active_jobs": worker.active_jobs,
            "status": worker.status.value,
            "protocol_version": worker.protocol_version,
            "worker_version": worker.worker_version,
            "draining": worker.draining,
            "locality_refs": list(worker.locality_refs),
        },
        adapter_metadata=worker.adapter_metadata,
    )


def _capability_refs(capabilities: tuple[Capability, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(capability.name for capability in capabilities))


def _reported_capabilities(
    refs: tuple[str, ...],
    kind: CapabilityKind,
) -> tuple[Capability, ...]:
    return tuple(
        Capability(
            name=ref,
            kind=kind,
            attributes={"source": "distributed-runtime"},
        )
        for ref in refs
    )


def _metadata_string(metadata: dict[str, JsonValue], key: str) -> str | None:
    value = metadata.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _merge_adapter_metadata(
    existing: tuple[AdapterMetadata, ...],
    incoming: tuple[AdapterMetadata, ...],
) -> tuple[AdapterMetadata, ...]:
    by_namespace = {item.namespace: item for item in existing}
    by_namespace.update({item.namespace: item for item in incoming})
    return tuple(by_namespace[namespace] for namespace in sorted(by_namespace))


def _optional_node(runtime: DistributedRuntime, node_id: str) -> NodeRecord | None:
    try:
        return runtime.registry.get_node(node_id)
    except RegistryError:
        return None


def _optional_worker(runtime: DistributedRuntime, worker_id: str) -> WorkerRecord | None:
    try:
        return runtime.registry.get_worker(worker_id)
    except RegistryError:
        return None


def _provider_worker_job_id(worker_id: str, run_id: str) -> str:
    value = uuid5(NAMESPACE_URL, f"ai-multi-agent-platform:{worker_id}:{run_id}")
    return f"worker_job_{value}"
