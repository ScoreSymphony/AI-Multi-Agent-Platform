"""Control Plane projections and administrative commands for issue #14."""

from __future__ import annotations

from datetime import datetime

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .models import AcceleratorResource, JobRequirements, NodeRecord, ResourceSnapshot, WorkerRecord
from .registry import RegistryError
from .runtime import DispatchRecord, DistributedRuntime

NODE_COLLECTION = "nodes"
NODE_TYPE = "node"
WORKER_COLLECTION = "workers"
WORKER_TYPE = "worker"
WORKER_JOB_COLLECTION = "worker-jobs"

DISTRIBUTED_ADMIN_COMMANDS = (
    "node.drain",
    "node.undrain",
    "node.maintenance-enable",
    "node.maintenance-disable",
    "worker.drain",
    "worker.undrain",
)


class NodeResourceService:
    """Read-only canonical runtime-state projection for participating Nodes."""

    def __init__(self, runtime: DistributedRuntime) -> None:
        self.runtime = runtime

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_node_resource(node) for node in self.runtime.registry.list_nodes())

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        """Enumerate privacy-safe canonical Node metadata for derived global Search."""

        return tuple(_node_search_resource(node) for node in self.runtime.registry.list_nodes())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        try:
            node = self.runtime.registry.get_node(resource_id)
        except RegistryError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"node not found: {resource_id}") from exc
        return _node_resource(node)


class WorkerResourceService:
    """Read-only canonical runtime-state projection for schedulable Workers."""

    def __init__(self, runtime: DistributedRuntime) -> None:
        self.runtime = runtime

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_worker_resource(worker) for worker in self.runtime.registry.list_workers())

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        """Enumerate privacy-safe canonical Worker metadata for derived global Search."""

        return tuple(
            _worker_search_resource(worker) for worker in self.runtime.registry.list_workers()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        try:
            worker = self.runtime.registry.get_worker(resource_id)
        except RegistryError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"worker not found: {resource_id}") from exc
        return _worker_resource(worker)


class WorkerJobResourceService:
    """Read-only dispatch ownership/reconciliation projection without secret references."""

    # Worker jobs are scheduler/lifecycle state, not part of #288 global discovery.
    # Keeping them out also prevents Search from becoming a second dispatch authority.
    search_indexable = False

    def __init__(self, runtime: DistributedRuntime) -> None:
        self.runtime = runtime

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_worker_job_resource(record) for record in self.runtime.records())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        try:
            record = self.runtime.get_record(resource_id)
        except RegistryError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND, f"worker job not found: {resource_id}"
            ) from exc
        return _worker_job_resource(record)


class DistributedAdminCommandHandlers:
    """Administrative mutations routed through the generic #15-authorized command seam."""

    def __init__(self, runtime: DistributedRuntime) -> None:
        self.runtime = runtime

    async def drain_node(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_empty_payload(payload)
        return _node_resource(self._node_draining(resource_ref, True))

    async def undrain_node(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_empty_payload(payload)
        return _node_resource(self._node_draining(resource_ref, False))

    async def enable_node_maintenance(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_empty_payload(payload)
        try:
            return _node_resource(self.runtime.set_node_maintenance(resource_ref, maintenance=True))
        except RegistryError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"node not found: {resource_ref}") from exc

    async def disable_node_maintenance(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_empty_payload(payload)
        try:
            return _node_resource(
                self.runtime.set_node_maintenance(resource_ref, maintenance=False)
            )
        except RegistryError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"node not found: {resource_ref}") from exc

    async def drain_worker(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_empty_payload(payload)
        return _worker_resource(self._worker_draining(resource_ref, True))

    async def undrain_worker(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_empty_payload(payload)
        return _worker_resource(self._worker_draining(resource_ref, False))

    def _node_draining(self, node_id: str, draining: bool) -> NodeRecord:
        try:
            return self.runtime.set_node_draining(node_id, draining=draining)
        except RegistryError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"node not found: {node_id}") from exc

    def _worker_draining(self, worker_id: str, draining: bool) -> WorkerRecord:
        try:
            return self.runtime.set_worker_draining(worker_id, draining=draining)
        except RegistryError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"worker not found: {worker_id}") from exc


def register_distributed_control_plane(
    control_plane: ControlPlane,
    runtime: DistributedRuntime,
) -> None:
    """Register #14 reads/admin commands without creating a second transport or auth stack."""

    control_plane.register_resource_service(NODE_COLLECTION, NodeResourceService(runtime))
    control_plane.register_resource_service(WORKER_COLLECTION, WorkerResourceService(runtime))
    control_plane.register_resource_service(
        WORKER_JOB_COLLECTION,
        WorkerJobResourceService(runtime),
    )
    handlers = DistributedAdminCommandHandlers(runtime)
    control_plane.register_command("node.drain", handlers.drain_node)
    control_plane.register_command("node.undrain", handlers.undrain_node)
    control_plane.register_command("node.maintenance-enable", handlers.enable_node_maintenance)
    control_plane.register_command("node.maintenance-disable", handlers.disable_node_maintenance)
    control_plane.register_command("worker.drain", handlers.drain_worker)
    control_plane.register_command("worker.undrain", handlers.undrain_worker)


def _node_resource(node: NodeRecord) -> dict[str, JsonValue]:
    return {
        "id": node.node_id,
        "display_name": node.display_name,
        "status": node.status.value,
        "registered_at": _timestamp(node.registered_at),
        "last_heartbeat_at": _timestamp(node.last_heartbeat_at),
        "updated_at": _timestamp(node.updated_at),
        "labels": list(node.labels),
        "os_name": node.os_name,
        "platform": node.platform,
        "architecture": node.architecture,
        "resources": _resources(node.resources),
        "supported_runtimes": list(node.supported_runtimes),
        "model_refs": list(node.model_refs),
        "capability_refs": list(node.capability_refs),
        "worker_refs": list(node.worker_refs),
        "trust_level": node.trust_level,
        "draining": node.draining,
        "maintenance": node.maintenance,
        "network_available": node.network_available,
        "locality_refs": list(node.locality_refs),
    }


def _worker_resource(worker: WorkerRecord) -> dict[str, JsonValue]:
    return {
        "id": worker.worker_id,
        "node_id": worker.node_id,
        "worker_type": worker.worker_type,
        "supported_executors": list(worker.supported_executors),
        "capability_refs": list(worker.capability_refs),
        "supported_runtimes": list(worker.supported_runtimes),
        "model_refs": list(worker.model_refs),
        "concurrency_limit": worker.concurrency_limit,
        "active_jobs": worker.active_jobs,
        "status": worker.status.value,
        "protocol_version": worker.protocol_version,
        "worker_version": worker.worker_version,
        "registered_at": _timestamp(worker.registered_at),
        "last_heartbeat_at": _timestamp(worker.last_heartbeat_at),
        "updated_at": _timestamp(worker.updated_at),
        "draining": worker.draining,
        "locality_refs": list(worker.locality_refs),
    }


def _node_search_resource(node: NodeRecord) -> dict[str, JsonValue]:
    """Small Search projection derived solely from the canonical Node record."""

    aliases = _search_terms(
        *node.supported_runtimes,
        *node.model_refs,
        node.os_name,
        node.platform,
        node.architecture,
        node.trust_level,
        "draining" if node.draining else None,
        "maintenance" if node.maintenance else None,
        "network-available" if node.network_available else "network-unavailable",
        *_resource_class_terms(node.resources),
    )
    return {
        "type": NODE_TYPE,
        "id": node.node_id,
        "display_name": node.display_name,
        "status": node.status.value,
        "labels": list(node.labels),
        "updated_at": _timestamp(node.updated_at),
        "aliases": list(aliases),
        "capabilities": list(node.capability_refs),
    }


def _worker_search_resource(worker: WorkerRecord) -> dict[str, JsonValue]:
    """Small Search projection derived solely from the canonical Worker record."""

    aliases = _search_terms(
        worker.node_id,
        worker.worker_type,
        *worker.supported_executors,
        *worker.supported_runtimes,
        *worker.model_refs,
        worker.protocol_version,
        worker.worker_version,
        "draining" if worker.draining else None,
    )
    return {
        "type": WORKER_TYPE,
        "id": worker.worker_id,
        "status": worker.status.value,
        "updated_at": _timestamp(worker.updated_at),
        "aliases": list(aliases),
        "capabilities": list(worker.capability_refs),
    }


def _resource_class_terms(resources: ResourceSnapshot) -> tuple[str, ...]:
    """Describe resource classes without indexing provider-private infrastructure IDs."""

    values: list[str | None] = []
    if resources.cpu_cores_total > 0:
        values.append("cpu")
    if resources.ram_total_bytes > 0:
        values.append("ram")
    if resources.storage_total_bytes > 0:
        values.append("storage")
    for accelerator in resources.accelerators:
        values.extend(("accelerator", accelerator.kind, accelerator.vendor, accelerator.model))
    return _search_terms(*values)


def _search_terms(*values: str | None) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(normalized)
    return tuple(terms)


def _worker_job_resource(record: DispatchRecord) -> dict[str, JsonValue]:
    job = record.job
    snapshot = record.snapshot
    result = record.result
    result_projection: dict[str, JsonValue] | None = None
    if result is not None:
        result_projection = {
            "status": result.status.value,
            "artifact_refs": list(result.artifact_refs),
            "evidence_refs": list(result.evidence_refs),
            "error_category": result.error_category,
            "completed_at": _timestamp(result.completed_at),
            "execution_status": (
                None if result.execution is None else result.execution.status.value
            ),
        }
    return {
        "id": job.worker_job_id,
        "worker_id": record.worker_id,
        "reservation_id": record.reservation_id,
        "state": record.state.value,
        "run_id": job.execution.run_id,
        "subject_type": job.execution.subject_type,
        "subject_id": job.execution.subject_id,
        "workspace_ref": job.workspace_ref,
        "snapshot_ref": job.snapshot_ref,
        "artifact_refs": list(job.artifact_refs),
        "actor_ref": job.actor_ref,
        "cancellation_ref": job.cancellation_ref,
        "timeout_seconds": job.timeout_seconds,
        "dispatch_attempt": job.dispatch_attempt,
        "idempotency_key": job.idempotency_key,
        "trace_parent": job.trace_parent,
        "requirements": _requirements(job.requirements),
        "execution_status": None if snapshot is None else snapshot.status.value,
        "result": result_projection,
        "last_error": record.last_error,
    }


def _requirements(requirements: JobRequirements) -> dict[str, JsonValue]:
    return {
        "executor_type": requirements.executor_type,
        "capability_refs": list(requirements.capability_refs),
        "cpu_cores_min": requirements.cpu_cores_min,
        "ram_min_bytes": requirements.ram_min_bytes,
        "storage_min_bytes": requirements.storage_min_bytes,
        "gpu": requirements.gpu,
        "vram_min_bytes": requirements.vram_min_bytes,
        "model_ref": requirements.model_ref,
        "runtime": requirements.runtime,
        "os_name": requirements.os_name,
        "network_required": requirements.network_required,
        "required_labels": list(requirements.required_labels),
        "preferred_labels": list(requirements.preferred_labels),
        "preferred_node_ids": list(requirements.preferred_node_ids),
        "preferred_worker_ids": list(requirements.preferred_worker_ids),
        "anti_affinity_node_ids": list(requirements.anti_affinity_node_ids),
        "allowed_trust_levels": list(requirements.allowed_trust_levels),
        "locality_refs": list(requirements.locality_refs),
        "concurrency_units": requirements.concurrency_units,
    }


def _resources(resources: ResourceSnapshot) -> dict[str, JsonValue]:
    return {
        "cpu_cores_total": resources.cpu_cores_total,
        "cpu_cores_available": resources.cpu_cores_available,
        "ram_total_bytes": resources.ram_total_bytes,
        "ram_available_bytes": resources.ram_available_bytes,
        "storage_total_bytes": resources.storage_total_bytes,
        "storage_available_bytes": resources.storage_available_bytes,
        "accelerators": [_accelerator(item) for item in resources.accelerators],
    }


def _accelerator(accelerator: AcceleratorResource) -> dict[str, JsonValue]:
    return {
        "accelerator_id": accelerator.accelerator_id,
        "kind": accelerator.kind,
        "vendor": accelerator.vendor,
        "model": accelerator.model,
        "memory_total_bytes": accelerator.memory_total_bytes,
        "memory_available_bytes": accelerator.memory_available_bytes,
    }


def _timestamp(value: datetime) -> str:
    return value.isoformat()


def _require_empty_payload(payload: dict[str, JsonValue]) -> None:
    if payload:
        fields: list[JsonValue] = []
        fields.extend(sorted(payload))
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "distributed administrative command does not accept a payload",
            details={"fields": fields},
        )
