"""Canonical, deployment-neutral distributed runtime contracts for issue #14."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from ai_multi_agent_platform.contracts.types import (
    AdapterMetadata,
    ExecutionRequest,
    ExecutionSnapshot,
)
from ai_multi_agent_platform.domain import RunStatus, new_id, validate_id

WORKER_PROTOCOL_VERSION = "1.0"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class NodeStatus(StrEnum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"


class WorkerStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    OFFLINE = "offline"


class ReservationStatus(StrEnum):
    RESERVED = "reserved"
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"


class JobResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class RejectionCode(StrEnum):
    NODE_OFFLINE = "node_offline"
    NODE_UNHEALTHY = "node_unhealthy"
    NODE_DRAINING = "node_draining"
    WORKER_OFFLINE = "worker_offline"
    WORKER_UNHEALTHY = "worker_unhealthy"
    WORKER_DRAINING = "worker_draining"
    EXECUTOR_UNSUPPORTED = "executor_unsupported"
    CAPABILITY_UNSUPPORTED = "capability_unsupported"
    RUNTIME_UNSUPPORTED = "runtime_unsupported"
    OS_UNSUPPORTED = "os_unsupported"
    CPU_INSUFFICIENT = "cpu_insufficient"
    RAM_INSUFFICIENT = "ram_insufficient"
    STORAGE_INSUFFICIENT = "storage_insufficient"
    GPU_REQUIRED = "gpu_required"
    VRAM_INSUFFICIENT = "vram_insufficient"
    MODEL_UNAVAILABLE = "model_unavailable"
    TRUST_INSUFFICIENT = "trust_insufficient"
    LABEL_MISMATCH = "label_mismatch"
    ANTI_AFFINITY = "anti_affinity"
    NETWORK_UNAVAILABLE = "network_unavailable"
    CONCURRENCY_EXHAUSTED = "concurrency_exhausted"


@dataclass(frozen=True, slots=True)
class AcceleratorResource:
    """Generic accelerator inventory; no vendor is architecturally privileged."""

    accelerator_id: str
    kind: str = "gpu"
    vendor: str | None = None
    model: str | None = None
    memory_total_bytes: int = 0
    memory_available_bytes: int = 0

    def __post_init__(self) -> None:
        if not self.accelerator_id.strip():
            raise ValueError("accelerator_id must not be blank")
        if not self.kind.strip():
            raise ValueError("accelerator kind must not be blank")
        if self.memory_total_bytes < 0 or self.memory_available_bytes < 0:
            raise ValueError("accelerator memory values must be non-negative")
        if self.memory_available_bytes > self.memory_total_bytes:
            raise ValueError("available accelerator memory cannot exceed total memory")


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    cpu_cores_total: float = 0.0
    cpu_cores_available: float = 0.0
    ram_total_bytes: int = 0
    ram_available_bytes: int = 0
    storage_total_bytes: int = 0
    storage_available_bytes: int = 0
    accelerators: tuple[AcceleratorResource, ...] = ()

    def __post_init__(self) -> None:
        numeric_values = (
            self.cpu_cores_total,
            self.cpu_cores_available,
            self.ram_total_bytes,
            self.ram_available_bytes,
            self.storage_total_bytes,
            self.storage_available_bytes,
        )
        if any(value < 0 for value in numeric_values):
            raise ValueError("resource values must be non-negative")
        if self.cpu_cores_available > self.cpu_cores_total:
            raise ValueError("available CPU cannot exceed total CPU")
        if self.ram_available_bytes > self.ram_total_bytes:
            raise ValueError("available RAM cannot exceed total RAM")
        if self.storage_available_bytes > self.storage_total_bytes:
            raise ValueError("available storage cannot exceed total storage")

    @property
    def max_available_accelerator_memory_bytes(self) -> int:
        return max(
            (accelerator.memory_available_bytes for accelerator in self.accelerators),
            default=0,
        )


@dataclass(frozen=True, slots=True)
class NodeRecord:
    """Canonical runtime record for one participating compute device."""

    node_id: str
    display_name: str
    resources: ResourceSnapshot = field(default_factory=ResourceSnapshot)
    status: NodeStatus = NodeStatus.ONLINE
    registered_at: datetime = field(default_factory=utc_now)
    last_heartbeat_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    labels: tuple[str, ...] = ()
    os_name: str | None = None
    platform: str | None = None
    architecture: str | None = None
    supported_runtimes: tuple[str, ...] = ()
    model_refs: tuple[str, ...] = ()
    capability_refs: tuple[str, ...] = ()
    worker_refs: tuple[str, ...] = ()
    trust_level: str = "standard"
    draining: bool = False
    maintenance: bool = False
    network_available: bool = True
    locality_refs: tuple[str, ...] = ()
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.node_id, "node")
        if not self.display_name.strip():
            raise ValueError("node display_name must not be blank")
        if not self.trust_level.strip():
            raise ValueError("node trust_level must not be blank")
        _require_aware(self.registered_at, "node registered_at")
        _require_aware(self.last_heartbeat_at, "node last_heartbeat_at")
        _require_aware(self.updated_at, "node updated_at")


@dataclass(frozen=True, slots=True)
class WorkerRecord:
    """Canonical schedulable process/service attached to a Node."""

    worker_id: str
    node_id: str
    worker_type: str = "execution"
    supported_executors: tuple[str, ...] = ()
    capability_refs: tuple[str, ...] = ()
    supported_runtimes: tuple[str, ...] = ()
    model_refs: tuple[str, ...] = ()
    concurrency_limit: int = 1
    active_jobs: int = 0
    status: WorkerStatus = WorkerStatus.HEALTHY
    protocol_version: str = WORKER_PROTOCOL_VERSION
    worker_version: str = "0"
    registered_at: datetime = field(default_factory=utc_now)
    last_heartbeat_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    draining: bool = False
    locality_refs: tuple[str, ...] = ()
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.worker_id, "worker")
        validate_id(self.node_id, "node")
        if not self.worker_type.strip():
            raise ValueError("worker_type must not be blank")
        if self.concurrency_limit < 1:
            raise ValueError("worker concurrency_limit must be >= 1")
        if self.active_jobs < 0:
            raise ValueError("worker active_jobs must be non-negative")
        if not self.protocol_version.strip():
            raise ValueError("worker protocol_version must not be blank")
        _require_aware(self.registered_at, "worker registered_at")
        _require_aware(self.last_heartbeat_at, "worker last_heartbeat_at")
        _require_aware(self.updated_at, "worker updated_at")


@dataclass(frozen=True, slots=True)
class JobRequirements:
    """Backend-neutral placement constraints and preferences."""

    executor_type: str | None = None
    capability_refs: tuple[str, ...] = ()
    cpu_cores_min: float = 0.0
    ram_min_bytes: int = 0
    storage_min_bytes: int = 0
    gpu: Literal["optional", "required", "forbidden"] = "optional"
    vram_min_bytes: int = 0
    model_ref: str | None = None
    runtime: str | None = None
    os_name: str | None = None
    network_required: bool = False
    required_labels: tuple[str, ...] = ()
    preferred_labels: tuple[str, ...] = ()
    preferred_node_ids: tuple[str, ...] = ()
    preferred_worker_ids: tuple[str, ...] = ()
    anti_affinity_node_ids: tuple[str, ...] = ()
    allowed_trust_levels: tuple[str, ...] = ()
    locality_refs: tuple[str, ...] = ()
    concurrency_units: int = 1

    def __post_init__(self) -> None:
        if self.cpu_cores_min < 0:
            raise ValueError("cpu_cores_min must be non-negative")
        if self.ram_min_bytes < 0 or self.storage_min_bytes < 0 or self.vram_min_bytes < 0:
            raise ValueError("byte requirements must be non-negative")
        if self.concurrency_units < 1:
            raise ValueError("concurrency_units must be >= 1")
        for node_id in self.preferred_node_ids + self.anti_affinity_node_ids:
            validate_id(node_id, "node")
        for worker_id in self.preferred_worker_ids:
            validate_id(worker_id, "worker")


@dataclass(frozen=True, slots=True)
class WorkerJobRequest:
    """Transport-neutral remote job contract."""

    execution: ExecutionRequest
    requirements: JobRequirements = field(default_factory=JobRequirements)
    worker_job_id: str = field(default_factory=lambda: new_id("worker_job"))
    workspace_ref: str | None = None
    snapshot_ref: str | None = None
    artifact_refs: tuple[str, ...] = ()
    secret_refs: tuple[str, ...] = ()
    actor_ref: str | None = None
    cancellation_ref: str | None = None
    timeout_seconds: float | None = None
    dispatch_attempt: int = 1
    idempotency_key: str | None = None
    trace_parent: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.worker_job_id, "worker_job")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if self.dispatch_attempt < 1:
            raise ValueError("dispatch_attempt must be >= 1")
        if self.idempotency_key is not None and not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")


@dataclass(frozen=True, slots=True)
class WorkerJobResult:
    worker_job_id: str
    worker_id: str
    status: JobResultStatus
    execution: ExecutionSnapshot | None = None
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    error_category: str | None = None
    completed_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.worker_job_id, "worker_job")
        validate_id(self.worker_id, "worker")
        if self.execution is not None:
            expected_execution_status = {
                JobResultStatus.SUCCEEDED: RunStatus.SUCCEEDED,
                JobResultStatus.FAILED: RunStatus.FAILED,
                JobResultStatus.CANCELLED: RunStatus.CANCELLED,
                JobResultStatus.TIMED_OUT: RunStatus.TIMED_OUT,
            }[self.status]
            if self.execution.status is not expected_execution_status:
                raise ValueError(
                    "worker result status must match its terminal execution snapshot status"
                )


@dataclass(frozen=True, slots=True)
class RegistrationRequest:
    node: NodeRecord
    workers: tuple[WorkerRecord, ...] = ()
    protocol_version: str = WORKER_PROTOCOL_VERSION
    service_identity_ref: str | None = None

    def __post_init__(self) -> None:
        if not self.protocol_version.strip():
            raise ValueError("protocol_version must not be blank")
        if any(worker.node_id != self.node.node_id for worker in self.workers):
            raise ValueError("all registered workers must belong to the registered node")


@dataclass(frozen=True, slots=True)
class Heartbeat:
    node_id: str
    observed_at: datetime = field(default_factory=utc_now)
    sequence: int = 1
    resources: ResourceSnapshot | None = None
    node_status: NodeStatus | None = None
    workers: tuple[WorkerRecord, ...] = ()
    protocol_version: str = WORKER_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        validate_id(self.node_id, "node")
        if self.sequence < 1:
            raise ValueError("heartbeat sequence must be >= 1")
        _require_aware(self.observed_at, "heartbeat observed_at")
        if any(worker.node_id != self.node_id for worker in self.workers):
            raise ValueError("heartbeat worker node_id mismatch")


@dataclass(frozen=True, slots=True)
class Reservation:
    worker_job_id: str
    worker_id: str
    node_id: str
    cpu_cores: float
    ram_bytes: int
    storage_bytes: int
    concurrency_units: int
    accelerator_id: str | None = None
    vram_bytes: int = 0
    reservation_id: str = field(default_factory=lambda: new_id("reservation"))
    created_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None
    status: ReservationStatus = ReservationStatus.RESERVED

    def __post_init__(self) -> None:
        validate_id(self.reservation_id, "reservation")
        validate_id(self.worker_job_id, "worker_job")
        validate_id(self.worker_id, "worker")
        validate_id(self.node_id, "node")
        if self.cpu_cores < 0 or self.ram_bytes < 0 or self.storage_bytes < 0:
            raise ValueError("reserved resources must be non-negative")
        if self.vram_bytes < 0:
            raise ValueError("reserved VRAM must be non-negative")
        if self.vram_bytes > 0 and self.accelerator_id is None:
            raise ValueError("VRAM reservation requires an accelerator_id")
        if self.accelerator_id is not None and not self.accelerator_id.strip():
            raise ValueError("accelerator_id must not be blank")
        if self.concurrency_units < 1:
            raise ValueError("reserved concurrency_units must be >= 1")


@dataclass(frozen=True, slots=True)
class RejectionReason:
    code: RejectionCode
    message: str


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    worker_id: str
    node_id: str
    accepted: bool
    score: int = 0
    reasons: tuple[RejectionReason, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.worker_id, "worker")
        validate_id(self.node_id, "node")


@dataclass(frozen=True, slots=True)
class SchedulingDecision:
    worker_job_id: str
    selected_worker_id: str | None
    evaluations: tuple[CandidateEvaluation, ...]

    def __post_init__(self) -> None:
        validate_id(self.worker_job_id, "worker_job")
        if self.selected_worker_id is not None:
            validate_id(self.selected_worker_id, "worker")
