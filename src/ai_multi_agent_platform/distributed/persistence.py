"""Durable reference persistence for distributed registry and dispatch ownership."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal, Protocol, cast

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    OperationContext,
    OperationControl,
    RetryMode,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import RunStatus

from .models import (
    AcceleratorResource,
    JobRequirements,
    JobResultStatus,
    NodeRecord,
    NodeStatus,
    Reservation,
    ReservationStatus,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerJobResult,
    WorkerRecord,
    WorkerStatus,
)
from .registry import DistributedRegistry, RegistrySnapshot
from .runtime import DispatchRecord, DispatchState, DistributedRuntime

DISTRIBUTED_STATE_SCHEMA_VERSION = "3"
_SUPPORTED_DISTRIBUTED_STATE_SCHEMA_VERSIONS = frozenset({"1", "2", "3"})


class DistributedStateStore(Protocol):
    """Replaceable persistence boundary for control-side distributed runtime state."""

    def save(self, registry: DistributedRegistry, runtime: DistributedRuntime) -> None: ...

    def restore(self, registry: DistributedRegistry, runtime: DistributedRuntime) -> bool: ...


class JsonDistributedStateStore:
    """Atomically replace one JSON snapshot for restart-safe reference operation."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, registry: DistributedRegistry, runtime: DistributedRuntime) -> None:
        snapshot = registry.snapshot()
        document: dict[str, JsonValue] = {
            "schema_version": DISTRIBUTED_STATE_SCHEMA_VERSION,
            "registry": {
                "nodes": [_encode_value(item) for item in snapshot.nodes],
                "workers": [_encode_value(item) for item in snapshot.workers],
                "heartbeat_sequences": [
                    {"node_id": node_id, "sequence": sequence}
                    for node_id, sequence in snapshot.heartbeat_sequences
                ],
                "reservations": [_encode_value(item) for item in snapshot.reservations],
            },
            "dispatch_records": [_encode_value(item) for item in runtime.records()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def restore(self, registry: DistributedRegistry, runtime: DistributedRuntime) -> bool:
        if not self.path.exists():
            return False
        raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        document = _object(raw, "distributed state document")
        version = _string(_required(document, "schema_version"), "schema_version")
        if version not in _SUPPORTED_DISTRIBUTED_STATE_SCHEMA_VERSIONS:
            raise ValueError(
                "unsupported distributed state schema version: "
                f"{version!r}; expected one of "
                f"{sorted(_SUPPORTED_DISTRIBUTED_STATE_SCHEMA_VERSIONS)!r}"
            )

        registry_document = _object(_required(document, "registry"), "registry")
        registry_snapshot = RegistrySnapshot(
            nodes=tuple(
                _node_record(item)
                for item in _array(_required(registry_document, "nodes"), "nodes")
            ),
            workers=tuple(
                _worker_record(item)
                for item in _array(_required(registry_document, "workers"), "workers")
            ),
            heartbeat_sequences=tuple(
                _heartbeat_sequence(item)
                for item in _array(
                    _required(registry_document, "heartbeat_sequences"),
                    "heartbeat_sequences",
                )
            ),
            reservations=tuple(
                _reservation(item)
                for item in _array(
                    _required(registry_document, "reservations"),
                    "reservations",
                )
            ),
        )
        records = tuple(
            _dispatch_record(item)
            for item in _array(_required(document, "dispatch_records"), "dispatch_records")
        )
        if len({record.job.worker_job_id for record in records}) != len(records):
            raise ValueError("distributed state contains duplicate worker job records")

        registry.restore_snapshot(registry_snapshot)
        runtime.restore_records(records)
        return True


def _encode_value(value: object) -> JsonValue:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return cast(str, value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _encode_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        encoded: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("distributed state mappings require string keys")
            encoded[key] = _encode_value(item)
        return encoded
    if isinstance(value, tuple | list):
        return [_encode_value(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported distributed state value: {type(value).__name__}")


def _node_record(value: JsonValue) -> NodeRecord:
    data = _object(value, "NodeRecord")
    registered_at = _datetime(_required(data, "registered_at"), "registered_at")
    last_heartbeat_at = _datetime(
        _required(data, "last_heartbeat_at"),
        "last_heartbeat_at",
    )
    updated_raw = data.get("updated_at")
    updated_at = (
        max(registered_at, last_heartbeat_at)
        if updated_raw is None
        else _datetime(updated_raw, "updated_at")
    )
    return NodeRecord(
        node_id=_required_string(data, "node_id"),
        display_name=_required_string(data, "display_name"),
        resources=_resource_snapshot(_required(data, "resources")),
        status=NodeStatus(_required_string(data, "status")),
        registered_at=registered_at,
        last_heartbeat_at=last_heartbeat_at,
        updated_at=updated_at,
        labels=_string_tuple(_required(data, "labels"), "labels"),
        os_name=_optional_string(data.get("os_name"), "os_name"),
        platform=_optional_string(data.get("platform"), "platform"),
        architecture=_optional_string(data.get("architecture"), "architecture"),
        supported_runtimes=_string_tuple(
            _required(data, "supported_runtimes"),
            "supported_runtimes",
        ),
        model_refs=_string_tuple(_required(data, "model_refs"), "model_refs"),
        capability_refs=_string_tuple(
            _required(data, "capability_refs"),
            "capability_refs",
        ),
        worker_refs=_string_tuple(_required(data, "worker_refs"), "worker_refs"),
        trust_level=_required_string(data, "trust_level"),
        draining=_boolean(_required(data, "draining"), "draining"),
        maintenance=_boolean(_required(data, "maintenance"), "maintenance"),
        network_available=_boolean(
            _required(data, "network_available"),
            "network_available",
        ),
        locality_refs=_string_tuple(_required(data, "locality_refs"), "locality_refs"),
        adapter_metadata=_adapter_metadata_tuple(
            _required(data, "adapter_metadata"),
            "adapter_metadata",
        ),
    )


def _worker_record(value: JsonValue) -> WorkerRecord:
    data = _object(value, "WorkerRecord")
    registered_at = _datetime(_required(data, "registered_at"), "registered_at")
    last_heartbeat_at = _datetime(
        _required(data, "last_heartbeat_at"),
        "last_heartbeat_at",
    )
    updated_raw = data.get("updated_at")
    updated_at = (
        max(registered_at, last_heartbeat_at)
        if updated_raw is None
        else _datetime(updated_raw, "updated_at")
    )
    return WorkerRecord(
        worker_id=_required_string(data, "worker_id"),
        node_id=_required_string(data, "node_id"),
        worker_type=_required_string(data, "worker_type"),
        supported_executors=_string_tuple(
            _required(data, "supported_executors"),
            "supported_executors",
        ),
        capability_refs=_string_tuple(
            _required(data, "capability_refs"),
            "capability_refs",
        ),
        supported_runtimes=_string_tuple(
            _required(data, "supported_runtimes"),
            "supported_runtimes",
        ),
        model_refs=_string_tuple(_required(data, "model_refs"), "model_refs"),
        concurrency_limit=_integer(
            _required(data, "concurrency_limit"),
            "concurrency_limit",
        ),
        active_jobs=_integer(_required(data, "active_jobs"), "active_jobs"),
        status=WorkerStatus(_required_string(data, "status")),
        protocol_version=_required_string(data, "protocol_version"),
        worker_version=_required_string(data, "worker_version"),
        registered_at=registered_at,
        last_heartbeat_at=last_heartbeat_at,
        updated_at=updated_at,
        draining=_boolean(_required(data, "draining"), "draining"),
        locality_refs=_string_tuple(_required(data, "locality_refs"), "locality_refs"),
        adapter_metadata=_adapter_metadata_tuple(
            _required(data, "adapter_metadata"),
            "adapter_metadata",
        ),
    )


def _resource_snapshot(value: JsonValue) -> ResourceSnapshot:
    data = _object(value, "ResourceSnapshot")
    return ResourceSnapshot(
        cpu_cores_total=_number(_required(data, "cpu_cores_total"), "cpu_cores_total"),
        cpu_cores_available=_number(
            _required(data, "cpu_cores_available"),
            "cpu_cores_available",
        ),
        ram_total_bytes=_integer(_required(data, "ram_total_bytes"), "ram_total_bytes"),
        ram_available_bytes=_integer(
            _required(data, "ram_available_bytes"),
            "ram_available_bytes",
        ),
        storage_total_bytes=_integer(
            _required(data, "storage_total_bytes"),
            "storage_total_bytes",
        ),
        storage_available_bytes=_integer(
            _required(data, "storage_available_bytes"),
            "storage_available_bytes",
        ),
        accelerators=tuple(
            _accelerator(item) for item in _array(_required(data, "accelerators"), "accelerators")
        ),
    )


def _accelerator(value: JsonValue) -> AcceleratorResource:
    data = _object(value, "AcceleratorResource")
    return AcceleratorResource(
        accelerator_id=_required_string(data, "accelerator_id"),
        kind=_required_string(data, "kind"),
        vendor=_optional_string(data.get("vendor"), "vendor"),
        model=_optional_string(data.get("model"), "model"),
        memory_total_bytes=_integer(
            _required(data, "memory_total_bytes"),
            "memory_total_bytes",
        ),
        memory_available_bytes=_integer(
            _required(data, "memory_available_bytes"),
            "memory_available_bytes",
        ),
    )


def _reservation(value: JsonValue) -> Reservation:
    data = _object(value, "Reservation")
    expires_raw = data.get("expires_at")
    expires_at = None if expires_raw is None else _datetime(expires_raw, "expires_at")
    return Reservation(
        worker_job_id=_required_string(data, "worker_job_id"),
        worker_id=_required_string(data, "worker_id"),
        node_id=_required_string(data, "node_id"),
        cpu_cores=_number(_required(data, "cpu_cores"), "cpu_cores"),
        ram_bytes=_integer(_required(data, "ram_bytes"), "ram_bytes"),
        storage_bytes=_integer(_required(data, "storage_bytes"), "storage_bytes"),
        concurrency_units=_integer(
            _required(data, "concurrency_units"),
            "concurrency_units",
        ),
        accelerator_id=_optional_string(data.get("accelerator_id"), "accelerator_id"),
        vram_bytes=_integer(_required(data, "vram_bytes"), "vram_bytes"),
        reservation_id=_required_string(data, "reservation_id"),
        created_at=_datetime(_required(data, "created_at"), "created_at"),
        expires_at=expires_at,
        status=ReservationStatus(_required_string(data, "status")),
    )


def _dispatch_record(value: JsonValue) -> DispatchRecord:
    data = _object(value, "DispatchRecord")
    handle_raw = data.get("handle")
    snapshot_raw = data.get("snapshot")
    result_raw = data.get("result")
    return DispatchRecord(
        job=_worker_job_request(_required(data, "job")),
        worker_id=_required_string(data, "worker_id"),
        reservation_id=_required_string(data, "reservation_id"),
        state=DispatchState(_required_string(data, "state")),
        handle=None if handle_raw is None else _execution_handle(handle_raw),
        snapshot=None if snapshot_raw is None else _execution_snapshot(snapshot_raw),
        result=None if result_raw is None else _worker_job_result(result_raw),
        last_error=_optional_string(data.get("last_error"), "last_error"),
    )


def _worker_job_request(value: JsonValue) -> WorkerJobRequest:
    data = _object(value, "WorkerJobRequest")
    timeout_raw = data.get("timeout_seconds")
    timeout = None if timeout_raw is None else _number(timeout_raw, "timeout_seconds")
    return WorkerJobRequest(
        execution=_execution_request(_required(data, "execution")),
        requirements=_job_requirements(_required(data, "requirements")),
        worker_job_id=_required_string(data, "worker_job_id"),
        workspace_ref=_optional_string(data.get("workspace_ref"), "workspace_ref"),
        snapshot_ref=_optional_string(data.get("snapshot_ref"), "snapshot_ref"),
        artifact_refs=_string_tuple(_required(data, "artifact_refs"), "artifact_refs"),
        secret_refs=_string_tuple(_required(data, "secret_refs"), "secret_refs"),
        actor_ref=_optional_string(data.get("actor_ref"), "actor_ref"),
        cancellation_ref=_optional_string(data.get("cancellation_ref"), "cancellation_ref"),
        timeout_seconds=timeout,
        dispatch_attempt=_integer(_required(data, "dispatch_attempt"), "dispatch_attempt"),
        idempotency_key=_optional_string(data.get("idempotency_key"), "idempotency_key"),
        trace_parent=_optional_string(data.get("trace_parent"), "trace_parent"),
    )


def _worker_job_result(value: JsonValue) -> WorkerJobResult:
    data = _object(value, "WorkerJobResult")
    execution_raw = data.get("execution")
    return WorkerJobResult(
        worker_job_id=_required_string(data, "worker_job_id"),
        worker_id=_required_string(data, "worker_id"),
        status=JobResultStatus(_required_string(data, "status")),
        execution=None if execution_raw is None else _execution_snapshot(execution_raw),
        artifact_refs=_string_tuple(_required(data, "artifact_refs"), "artifact_refs"),
        evidence_refs=_string_tuple(_required(data, "evidence_refs"), "evidence_refs"),
        error_category=_optional_string(data.get("error_category"), "error_category"),
        completed_at=_datetime(_required(data, "completed_at"), "completed_at"),
    )


def _job_requirements(value: JsonValue) -> JobRequirements:
    data = _object(value, "JobRequirements")
    gpu_raw = _required_string(data, "gpu")
    if gpu_raw not in {"optional", "required", "forbidden"}:
        raise ValueError(f"invalid gpu requirement: {gpu_raw!r}")
    gpu = cast(Literal["optional", "required", "forbidden"], gpu_raw)
    return JobRequirements(
        executor_type=_optional_string(data.get("executor_type"), "executor_type"),
        capability_refs=_string_tuple(
            _required(data, "capability_refs"),
            "capability_refs",
        ),
        cpu_cores_min=_number(_required(data, "cpu_cores_min"), "cpu_cores_min"),
        ram_min_bytes=_integer(_required(data, "ram_min_bytes"), "ram_min_bytes"),
        storage_min_bytes=_integer(
            _required(data, "storage_min_bytes"),
            "storage_min_bytes",
        ),
        gpu=gpu,
        vram_min_bytes=_integer(_required(data, "vram_min_bytes"), "vram_min_bytes"),
        model_ref=_optional_string(data.get("model_ref"), "model_ref"),
        runtime=_optional_string(data.get("runtime"), "runtime"),
        os_name=_optional_string(data.get("os_name"), "os_name"),
        network_required=_boolean(
            _required(data, "network_required"),
            "network_required",
        ),
        required_labels=_string_tuple(
            _required(data, "required_labels"),
            "required_labels",
        ),
        preferred_labels=_string_tuple(
            _required(data, "preferred_labels"),
            "preferred_labels",
        ),
        preferred_node_ids=_string_tuple(
            _required(data, "preferred_node_ids"),
            "preferred_node_ids",
        ),
        preferred_worker_ids=_string_tuple(
            _required(data, "preferred_worker_ids"),
            "preferred_worker_ids",
        ),
        anti_affinity_node_ids=_string_tuple(
            _required(data, "anti_affinity_node_ids"),
            "anti_affinity_node_ids",
        ),
        allowed_trust_levels=_string_tuple(
            _required(data, "allowed_trust_levels"),
            "allowed_trust_levels",
        ),
        locality_refs=_string_tuple(_required(data, "locality_refs"), "locality_refs"),
        concurrency_units=_integer(
            _required(data, "concurrency_units"),
            "concurrency_units",
        ),
    )


def _execution_request(value: JsonValue) -> ExecutionRequest:
    data = _object(value, "ExecutionRequest")
    subject_type_raw = _required_string(data, "subject_type")
    if subject_type_raw not in {"task", "step"}:
        raise ValueError(f"invalid execution subject_type: {subject_type_raw!r}")
    subject_type = cast(Literal["task", "step"], subject_type_raw)
    return ExecutionRequest(
        run_id=_required_string(data, "run_id"),
        subject_type=subject_type,
        subject_id=_required_string(data, "subject_id"),
        context=_operation_context(_required(data, "context")),
        input=_object(_required(data, "input"), "execution input"),
    )


def _operation_context(value: JsonValue) -> OperationContext:
    data = _object(value, "OperationContext")
    return OperationContext(
        correlation_id=_required_string(data, "correlation_id"),
        causation_id=_optional_string(data.get("causation_id"), "causation_id"),
        owner_type=_optional_string(data.get("owner_type"), "owner_type"),
        owner_id=_optional_string(data.get("owner_id"), "owner_id"),
        project_id=_optional_string(data.get("project_id"), "project_id"),
        control=_operation_control(_required(data, "control")),
    )


def _operation_control(value: JsonValue) -> OperationControl:
    data = _object(value, "OperationControl")
    timeout_raw = data.get("timeout_seconds")
    timeout = None if timeout_raw is None else _number(timeout_raw, "timeout_seconds")
    return OperationControl(
        timeout_seconds=timeout,
        idempotency_key=_optional_string(data.get("idempotency_key"), "idempotency_key"),
        retry_mode=RetryMode(_required_string(data, "retry_mode")),
    )


def _execution_handle(value: JsonValue) -> ExecutionHandle:
    data = _object(value, "ExecutionHandle")
    return ExecutionHandle(
        run_id=_required_string(data, "run_id"),
        backend_ref=_optional_string(data.get("backend_ref"), "backend_ref"),
        adapter_metadata=_adapter_metadata_tuple(
            _required(data, "adapter_metadata"),
            "adapter_metadata",
        ),
    )


def _execution_snapshot(value: JsonValue) -> ExecutionSnapshot:
    data = _object(value, "ExecutionSnapshot")
    return ExecutionSnapshot(
        run_id=_required_string(data, "run_id"),
        status=RunStatus(_required_string(data, "status")),
        output=_object(_required(data, "output"), "execution output"),
        adapter_metadata=_adapter_metadata_tuple(
            _required(data, "adapter_metadata"),
            "adapter_metadata",
        ),
    )


def _adapter_metadata_tuple(value: JsonValue, label: str) -> tuple[AdapterMetadata, ...]:
    return tuple(_adapter_metadata(item) for item in _array(value, label))


def _adapter_metadata(value: JsonValue) -> AdapterMetadata:
    data = _object(value, "AdapterMetadata")
    return AdapterMetadata(
        namespace=_required_string(data, "namespace"),
        values=_object(_required(data, "values"), "adapter metadata values"),
    )


def _heartbeat_sequence(value: JsonValue) -> tuple[str, int]:
    data = _object(value, "heartbeat sequence")
    return (
        _required_string(data, "node_id"),
        _integer(_required(data, "sequence"), "sequence"),
    )


def _required(data: dict[str, JsonValue], key: str) -> JsonValue:
    try:
        return data[key]
    except KeyError as exc:
        raise ValueError(f"distributed state is missing required field {key!r}") from exc


def _required_string(data: dict[str, JsonValue], key: str) -> str:
    return _string(_required(data, key), key)


def _object(value: object, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object")
    return cast(dict[str, JsonValue], value)


def _array(value: JsonValue, label: str) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _string(value: JsonValue, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _optional_string(value: JsonValue | None, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _string_tuple(value: JsonValue, label: str) -> tuple[str, ...]:
    return tuple(_string(item, label) for item in _array(value, label))


def _boolean(value: JsonValue, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _integer(value: JsonValue, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _number(value: JsonValue, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} must be numeric")
    return float(value)


def _datetime(value: JsonValue, label: str) -> datetime:
    raw = _string(value, label)
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 datetime") from exc
