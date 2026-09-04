"""#35-backed transport adapter for canonical distributed Worker jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from typing import Literal, Protocol, cast, runtime_checkable

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
from ai_multi_agent_platform.messaging import (
    MessageKind,
    MessageTransport,
    Subscription,
    TraceContext,
    TransportEnvelope,
)
from ai_multi_agent_platform.security import redact_exception

from .models import (
    JobRequirements,
    JobResultStatus,
    WorkerJobRequest,
    WorkerJobResult,
)
from .registry import RegistryError
from .worker import WorkerDispatcher

WORKER_TRANSPORT_SCHEMA_VERSION = "1"
WORKER_COMMAND_TOPIC_PREFIX = "distributed.worker.commands"
WORKER_REPLY_TOPIC_PREFIX = "distributed.worker.replies"


def worker_command_topic(worker_id: str) -> str:
    return f"{WORKER_COMMAND_TOPIC_PREFIX}.{worker_id}"


@runtime_checkable
class WorkerResultProvider(Protocol):
    """Optional Worker-side result surface used by transport result requests."""

    async def result(self, worker_job_id: str) -> WorkerJobResult | None: ...


class RemoteWorkerTransportError(RegistryError):
    """Canonical remote Worker failure returned through the message transport."""

    def __init__(self, category: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable


class WorkerTransportCodec:
    """JSON-compatible codec for the transport-neutral Worker job/result contract."""

    @staticmethod
    def encode_job(job: WorkerJobRequest) -> dict[str, JsonValue]:
        return {
            "worker_job_id": job.worker_job_id,
            "execution": _encode_execution_request(job.execution),
            "requirements": _encode_requirements(job.requirements),
            "workspace_ref": job.workspace_ref,
            "snapshot_ref": job.snapshot_ref,
            "artifact_refs": list(job.artifact_refs),
            "secret_refs": list(job.secret_refs),
            "actor_ref": job.actor_ref,
            "cancellation_ref": job.cancellation_ref,
            "timeout_seconds": job.timeout_seconds,
            "dispatch_attempt": job.dispatch_attempt,
            "idempotency_key": job.idempotency_key,
            "trace_parent": job.trace_parent,
        }

    @staticmethod
    def decode_job(value: object) -> WorkerJobRequest:
        data = _mapping(value, "WorkerJobRequest")
        return WorkerJobRequest(
            worker_job_id=_required_string(data, "worker_job_id"),
            execution=_decode_execution_request(_required(data, "execution")),
            requirements=_decode_requirements(_required(data, "requirements")),
            workspace_ref=_optional_string(data.get("workspace_ref"), "workspace_ref"),
            snapshot_ref=_optional_string(data.get("snapshot_ref"), "snapshot_ref"),
            artifact_refs=_string_tuple(_required(data, "artifact_refs"), "artifact_refs"),
            secret_refs=_string_tuple(_required(data, "secret_refs"), "secret_refs"),
            actor_ref=_optional_string(data.get("actor_ref"), "actor_ref"),
            cancellation_ref=_optional_string(data.get("cancellation_ref"), "cancellation_ref"),
            timeout_seconds=_optional_number(data.get("timeout_seconds"), "timeout_seconds"),
            dispatch_attempt=_integer(_required(data, "dispatch_attempt"), "dispatch_attempt"),
            idempotency_key=_optional_string(data.get("idempotency_key"), "idempotency_key"),
            trace_parent=_optional_string(data.get("trace_parent"), "trace_parent"),
        )

    @staticmethod
    def encode_handle(handle: ExecutionHandle) -> dict[str, JsonValue]:
        return {
            "run_id": handle.run_id,
            "backend_ref": handle.backend_ref,
            "adapter_metadata": _encode_adapter_metadata(handle.adapter_metadata),
        }

    @staticmethod
    def decode_handle(value: object) -> ExecutionHandle:
        data = _mapping(value, "ExecutionHandle")
        return ExecutionHandle(
            run_id=_required_string(data, "run_id"),
            backend_ref=_optional_string(data.get("backend_ref"), "backend_ref"),
            adapter_metadata=_decode_adapter_metadata(
                _required(data, "adapter_metadata"), "adapter_metadata"
            ),
        )

    @staticmethod
    def encode_snapshot(snapshot: ExecutionSnapshot) -> dict[str, JsonValue]:
        return {
            "run_id": snapshot.run_id,
            "status": snapshot.status.value,
            "output": dict(snapshot.output),
            "adapter_metadata": _encode_adapter_metadata(snapshot.adapter_metadata),
        }

    @staticmethod
    def decode_snapshot(value: object) -> ExecutionSnapshot:
        data = _mapping(value, "ExecutionSnapshot")
        output = _mapping(_required(data, "output"), "output")
        return ExecutionSnapshot(
            run_id=_required_string(data, "run_id"),
            status=RunStatus(_required_string(data, "status")),
            output={key: cast(JsonValue, item) for key, item in output.items()},
            adapter_metadata=_decode_adapter_metadata(
                _required(data, "adapter_metadata"), "adapter_metadata"
            ),
        )

    @staticmethod
    def encode_result(result: WorkerJobResult) -> dict[str, JsonValue]:
        return {
            "worker_job_id": result.worker_job_id,
            "worker_id": result.worker_id,
            "status": result.status.value,
            "execution": (
                WorkerTransportCodec.encode_snapshot(result.execution)
                if result.execution is not None
                else None
            ),
            "artifact_refs": list(result.artifact_refs),
            "evidence_refs": list(result.evidence_refs),
            "error_category": result.error_category,
            "completed_at": result.completed_at.isoformat(),
        }

    @staticmethod
    def decode_result(value: object) -> WorkerJobResult:
        data = _mapping(value, "WorkerJobResult")
        execution_raw = data.get("execution")
        return WorkerJobResult(
            worker_job_id=_required_string(data, "worker_job_id"),
            worker_id=_required_string(data, "worker_id"),
            status=JobResultStatus(_required_string(data, "status")),
            execution=(
                None
                if execution_raw is None
                else WorkerTransportCodec.decode_snapshot(execution_raw)
            ),
            artifact_refs=_string_tuple(_required(data, "artifact_refs"), "artifact_refs"),
            evidence_refs=_string_tuple(_required(data, "evidence_refs"), "evidence_refs"),
            error_category=_optional_string(data.get("error_category"), "error_category"),
            completed_at=_datetime(_required(data, "completed_at"), "completed_at"),
        )


class TransportWorkerDispatcher:
    """Control-side Worker dispatcher implemented only through #35 MessageTransport."""

    def __init__(
        self,
        worker_id: str,
        transport: MessageTransport,
        *,
        client_id: str = "distributed-control-plane",
        response_timeout_seconds: float = 30.0,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if not client_id.strip():
            raise ValueError("transport client_id must not be blank")
        if response_timeout_seconds <= 0:
            raise ValueError("response_timeout_seconds must be greater than zero")
        self._worker_id = worker_id
        self._transport = transport
        self._client_id = client_id
        self._response_timeout_seconds = response_timeout_seconds

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        reply = await self._request(
            operation="dispatch",
            worker_job_id=job.worker_job_id,
            job=job,
            payload={"job": WorkerTransportCodec.encode_job(job)},
        )
        return WorkerTransportCodec.decode_handle(_required(reply, "handle"))

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        reply = await self._request(
            operation="get",
            worker_job_id=worker_job_id,
            payload={},
        )
        return WorkerTransportCodec.decode_snapshot(_required(reply, "snapshot"))

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        reply = await self._request(
            operation="cancel",
            worker_job_id=worker_job_id,
            payload={},
        )
        return WorkerTransportCodec.decode_snapshot(_required(reply, "snapshot"))

    async def result(self, worker_job_id: str) -> WorkerJobResult | None:
        reply = await self._request(
            operation="result",
            worker_job_id=worker_job_id,
            payload={},
        )
        result_raw = reply.get("result")
        if result_raw is None:
            return None
        return WorkerTransportCodec.decode_result(result_raw)

    async def _request(
        self,
        *,
        operation: Literal["dispatch", "get", "cancel", "result"],
        worker_job_id: str,
        payload: dict[str, JsonValue],
        job: WorkerJobRequest | None = None,
    ) -> Mapping[str, object]:
        correlation_id = (
            job.execution.context.correlation_id
            if job is not None
            else f"worker-job:{worker_job_id}"
        )
        task_id = (
            job.execution.subject_id
            if job is not None and job.execution.subject_type == "task"
            else None
        )
        run_id = job.execution.run_id if job is not None else None
        project_id = job.execution.context.project_id if job is not None else None
        causation_id = job.execution.context.causation_id if job is not None else None
        attempt = job.dispatch_attempt if job is not None else 1
        idempotency_key = f"{worker_job_id}:{operation}:{attempt}"
        command_payload: dict[str, JsonValue] = {
            "worker_id": self.worker_id,
            "worker_job_id": worker_job_id,
            "operation": operation,
            **payload,
        }
        command = TransportEnvelope(
            message_type=f"worker.{operation}",
            kind=MessageKind.COMMAND,
            payload_schema_version=WORKER_TRANSPORT_SCHEMA_VERSION,
            source_component=self._client_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            project_id=project_id,
            task_id=task_id,
            run_id=run_id,
            idempotency_key=idempotency_key,
            trace_context=TraceContext(),
            payload=command_payload,
        )
        reply_topic = f"{WORKER_REPLY_TOPIC_PREFIX}.{command.message_id}"
        command_payload["reply_topic"] = reply_topic
        command = TransportEnvelope(
            message_id=command.message_id,
            message_type=command.message_type,
            kind=command.kind,
            payload_schema_version=command.payload_schema_version,
            source_component=command.source_component,
            correlation_id=command.correlation_id,
            causation_id=command.causation_id,
            project_id=command.project_id,
            task_id=command.task_id,
            run_id=command.run_id,
            idempotency_key=command.idempotency_key,
            trace_context=command.trace_context,
            payload=command_payload,
        )
        subscription = self._transport.subscribe(
            Subscription(
                topic=reply_topic,
                consumer_id=f"{self._client_id}:{command.message_id}",
                consumer_group=f"request:{command.message_id}",
            )
        )
        timeout_seconds = (
            job.timeout_seconds
            if job is not None and job.timeout_seconds is not None
            else self._response_timeout_seconds
        )
        control = OperationControl(
            timeout_seconds=timeout_seconds,
            idempotency_key=idempotency_key,
            retry_mode=RetryMode.IDEMPOTENT,
        )
        try:
            await self._transport.publish(
                worker_command_topic(self.worker_id), command, control=control
            )
            try:
                async with asyncio.timeout(timeout_seconds):
                    delivery = await subscription.__anext__()
            except TimeoutError as exc:
                raise RemoteWorkerTransportError(
                    "response_timeout",
                    f"Worker transport response timed out for {worker_job_id}",
                    retryable=True,
                ) from exc
            await self._transport.ack(delivery)
            reply = delivery.envelope
            if reply.causation_id != command.message_id:
                raise RemoteWorkerTransportError(
                    "reply_mismatch",
                    "Worker transport reply causation does not match the command",
                )
            if reply.correlation_id != command.correlation_id:
                raise RemoteWorkerTransportError(
                    "reply_mismatch",
                    "Worker transport reply correlation does not match the command",
                )
            data = _mapping(reply.payload, "Worker transport reply")
            if _required_string(data, "worker_id") != self.worker_id:
                raise RemoteWorkerTransportError(
                    "reply_mismatch", "Worker transport reply came from a different Worker"
                )
            if _required_string(data, "worker_job_id") != worker_job_id:
                raise RemoteWorkerTransportError(
                    "reply_mismatch", "Worker transport reply belongs to a different Worker Job"
                )
            if reply.message_type == "worker.error":
                raise RemoteWorkerTransportError(
                    _required_string(data, "error_category"),
                    _required_string(data, "message"),
                    retryable=_boolean(data.get("retryable"), "retryable"),
                )
            return data
        finally:
            await subscription.aclose()


class WorkerTransportEndpoint:
    """Worker-side command consumer exposing one WorkerDispatcher over #35 transport."""

    def __init__(
        self,
        dispatcher: WorkerDispatcher,
        transport: MessageTransport,
        *,
        consumer_id: str | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._transport = transport
        self._consumer_id = consumer_id or f"endpoint:{dispatcher.worker_id}"
        if not self._consumer_id.strip():
            raise ValueError("Worker transport consumer_id must not be blank")

    @property
    def worker_id(self) -> str:
        return self._dispatcher.worker_id

    async def serve(self) -> None:
        subscription = self._transport.subscribe(
            Subscription(
                topic=worker_command_topic(self.worker_id),
                consumer_id=self._consumer_id,
                consumer_group=f"worker:{self.worker_id}",
            )
        )
        try:
            async for delivery in subscription:
                try:
                    await self._handle(delivery.envelope)
                except Exception:
                    await self._transport.nack(
                        delivery,
                        retry=True,
                        reason="worker_transport_reply_publish_failed",
                    )
                else:
                    await self._transport.ack(delivery)
        finally:
            await subscription.aclose()

    async def _handle(self, command: TransportEnvelope) -> None:
        data = _mapping(command.payload, "Worker transport command")
        reply_topic = _required_string(data, "reply_topic")
        if not reply_topic.startswith(f"{WORKER_REPLY_TOPIC_PREFIX}."):
            raise RegistryError("Worker transport reply topic is outside the canonical prefix")
        worker_id = _required_string(data, "worker_id")
        worker_job_id = _required_string(data, "worker_job_id")
        if worker_id != self.worker_id:
            await self._publish_error(
                command,
                reply_topic,
                worker_job_id,
                category="worker_identity_mismatch",
                message="Worker transport command targets a different Worker",
                retryable=False,
            )
            return
        operation = _required_string(data, "operation")
        try:
            if operation == "dispatch":
                job = WorkerTransportCodec.decode_job(_required(data, "job"))
                if job.worker_job_id != worker_job_id:
                    raise RegistryError("Worker transport command/job identity mismatch")
                handle = await self._dispatcher.dispatch(job)
                payload: dict[str, JsonValue] = {
                    "worker_id": self.worker_id,
                    "worker_job_id": worker_job_id,
                    "handle": WorkerTransportCodec.encode_handle(handle),
                }
                await self._publish_reply(command, reply_topic, "worker.dispatch.accepted", payload)
                return
            if operation == "get":
                snapshot = await self._dispatcher.get(worker_job_id)
                payload = {
                    "worker_id": self.worker_id,
                    "worker_job_id": worker_job_id,
                    "snapshot": WorkerTransportCodec.encode_snapshot(snapshot),
                }
                await self._publish_reply(command, reply_topic, "worker.snapshot", payload)
                return
            if operation == "cancel":
                snapshot = await self._dispatcher.cancel(worker_job_id)
                payload = {
                    "worker_id": self.worker_id,
                    "worker_job_id": worker_job_id,
                    "snapshot": WorkerTransportCodec.encode_snapshot(snapshot),
                }
                await self._publish_reply(command, reply_topic, "worker.snapshot", payload)
                return
            if operation == "result":
                if not isinstance(self._dispatcher, WorkerResultProvider):
                    raise RemoteWorkerTransportError(
                        "result_unsupported",
                        "Worker dispatcher does not expose terminal result retrieval",
                    )
                result = await self._dispatcher.result(worker_job_id)
                payload = {
                    "worker_id": self.worker_id,
                    "worker_job_id": worker_job_id,
                    "result": (
                        WorkerTransportCodec.encode_result(result) if result is not None else None
                    ),
                }
                await self._publish_reply(command, reply_topic, "worker.result", payload)
                return
            raise RegistryError(f"unsupported Worker transport operation: {operation}")
        except Exception as exc:
            category, retryable = _error_category(exc)
            await self._publish_error(
                command,
                reply_topic,
                worker_job_id,
                category=category,
                message=_safe_error_message(exc, category),
                retryable=retryable,
            )

    async def _publish_reply(
        self,
        command: TransportEnvelope,
        reply_topic: str,
        message_type: str,
        payload: dict[str, JsonValue],
    ) -> None:
        reply = TransportEnvelope(
            message_type=message_type,
            kind=MessageKind.NOTIFICATION,
            payload_schema_version=WORKER_TRANSPORT_SCHEMA_VERSION,
            source_component=f"distributed-worker:{self.worker_id}",
            correlation_id=command.correlation_id,
            causation_id=command.message_id,
            project_id=command.project_id,
            task_id=command.task_id,
            run_id=command.run_id,
            idempotency_key=f"reply:{command.message_id}",
            trace_context=command.trace_context,
            payload=payload,
        )
        await self._transport.publish(reply_topic, reply)

    async def _publish_error(
        self,
        command: TransportEnvelope,
        reply_topic: str,
        worker_job_id: str,
        *,
        category: str,
        message: str,
        retryable: bool,
    ) -> None:
        await self._publish_reply(
            command,
            reply_topic,
            "worker.error",
            {
                "worker_id": self.worker_id,
                "worker_job_id": worker_job_id,
                "error_category": category,
                "message": message or category,
                "retryable": retryable,
            },
        )


def _encode_execution_request(request: ExecutionRequest) -> dict[str, JsonValue]:
    return {
        "run_id": request.run_id,
        "subject_type": request.subject_type,
        "subject_id": request.subject_id,
        "context": _encode_operation_context(request.context),
        "input": dict(request.input),
    }


def _decode_execution_request(value: object) -> ExecutionRequest:
    data = _mapping(value, "ExecutionRequest")
    subject_type_raw = _required_string(data, "subject_type")
    if subject_type_raw not in {"task", "step"}:
        raise ValueError(f"invalid execution subject_type: {subject_type_raw!r}")
    subject_type = cast(Literal["task", "step"], subject_type_raw)
    input_data = _mapping(_required(data, "input"), "input")
    return ExecutionRequest(
        run_id=_required_string(data, "run_id"),
        subject_type=subject_type,
        subject_id=_required_string(data, "subject_id"),
        context=_decode_operation_context(_required(data, "context")),
        input={key: cast(JsonValue, item) for key, item in input_data.items()},
    )


def _encode_operation_context(context: OperationContext) -> dict[str, JsonValue]:
    return {
        "correlation_id": context.correlation_id,
        "causation_id": context.causation_id,
        "owner_type": context.owner_type,
        "owner_id": context.owner_id,
        "project_id": context.project_id,
        "control": {
            "timeout_seconds": context.control.timeout_seconds,
            "idempotency_key": context.control.idempotency_key,
            "retry_mode": context.control.retry_mode.value,
        },
    }


def _decode_operation_context(value: object) -> OperationContext:
    data = _mapping(value, "OperationContext")
    control_data = _mapping(_required(data, "control"), "OperationControl")
    return OperationContext(
        correlation_id=_required_string(data, "correlation_id"),
        causation_id=_optional_string(data.get("causation_id"), "causation_id"),
        owner_type=_optional_string(data.get("owner_type"), "owner_type"),
        owner_id=_optional_string(data.get("owner_id"), "owner_id"),
        project_id=_optional_string(data.get("project_id"), "project_id"),
        control=OperationControl(
            timeout_seconds=_optional_number(
                control_data.get("timeout_seconds"), "timeout_seconds"
            ),
            idempotency_key=_optional_string(
                control_data.get("idempotency_key"), "idempotency_key"
            ),
            retry_mode=RetryMode(_required_string(control_data, "retry_mode")),
        ),
    )


def _encode_requirements(requirements: JobRequirements) -> dict[str, JsonValue]:
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


def _decode_requirements(value: object) -> JobRequirements:
    data = _mapping(value, "JobRequirements")
    gpu_raw = _required_string(data, "gpu")
    if gpu_raw not in {"optional", "required", "forbidden"}:
        raise ValueError(f"invalid GPU requirement: {gpu_raw!r}")
    gpu = cast(Literal["optional", "required", "forbidden"], gpu_raw)
    return JobRequirements(
        executor_type=_optional_string(data.get("executor_type"), "executor_type"),
        capability_refs=_string_tuple(_required(data, "capability_refs"), "capability_refs"),
        cpu_cores_min=_number(_required(data, "cpu_cores_min"), "cpu_cores_min"),
        ram_min_bytes=_integer(_required(data, "ram_min_bytes"), "ram_min_bytes"),
        storage_min_bytes=_integer(_required(data, "storage_min_bytes"), "storage_min_bytes"),
        gpu=gpu,
        vram_min_bytes=_integer(_required(data, "vram_min_bytes"), "vram_min_bytes"),
        model_ref=_optional_string(data.get("model_ref"), "model_ref"),
        runtime=_optional_string(data.get("runtime"), "runtime"),
        os_name=_optional_string(data.get("os_name"), "os_name"),
        network_required=_boolean(data.get("network_required"), "network_required"),
        required_labels=_string_tuple(_required(data, "required_labels"), "required_labels"),
        preferred_labels=_string_tuple(_required(data, "preferred_labels"), "preferred_labels"),
        preferred_node_ids=_string_tuple(
            _required(data, "preferred_node_ids"), "preferred_node_ids"
        ),
        preferred_worker_ids=_string_tuple(
            _required(data, "preferred_worker_ids"), "preferred_worker_ids"
        ),
        anti_affinity_node_ids=_string_tuple(
            _required(data, "anti_affinity_node_ids"), "anti_affinity_node_ids"
        ),
        allowed_trust_levels=_string_tuple(
            _required(data, "allowed_trust_levels"), "allowed_trust_levels"
        ),
        locality_refs=_string_tuple(_required(data, "locality_refs"), "locality_refs"),
        concurrency_units=_integer(_required(data, "concurrency_units"), "concurrency_units"),
    )


def _encode_adapter_metadata(values: tuple[AdapterMetadata, ...]) -> list[JsonValue]:
    return [{"namespace": value.namespace, "values": dict(value.values)} for value in values]


def _decode_adapter_metadata(value: object, name: str) -> tuple[AdapterMetadata, ...]:
    items = _sequence(value, name)
    result: list[AdapterMetadata] = []
    for item in items:
        data = _mapping(item, "AdapterMetadata")
        values = _mapping(_required(data, "values"), "AdapterMetadata.values")
        result.append(
            AdapterMetadata(
                namespace=_required_string(data, "namespace"),
                values={key: cast(JsonValue, item_value) for key, item_value in values.items()},
            )
        )
    return tuple(result)


def _error_category(error: Exception) -> tuple[str, bool]:
    if isinstance(error, RemoteWorkerTransportError):
        return error.category, error.retryable
    if isinstance(error, (RegistryError, ValueError)):
        return "worker_contract_error", False
    if isinstance(error, (TimeoutError, ConnectionError)):
        return "worker_unavailable", True
    return "worker_execution_error", False


def _safe_error_message(error: Exception, category: str) -> str:
    if isinstance(error, RemoteWorkerTransportError):
        return redact_exception(error)
    if isinstance(error, RegistryError):
        return redact_exception(error)
    if category == "worker_contract_error":
        return "Worker transport contract rejected the request"
    if category == "worker_unavailable":
        return "Worker is unavailable"
    return "Worker execution failed"


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"{name} keys must be strings")
        result[key] = item
    return result


def _sequence(value: object, name: str) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be an array")
    return tuple(value)


def _required(data: Mapping[str, object], name: str) -> object:
    if name not in data:
        raise ValueError(f"{name} is required")
    return data[name]


def _required_string(data: Mapping[str, object], name: str) -> str:
    value = _required(data, name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    items = _sequence(value, name)
    if any(not isinstance(item, str) for item in items):
        raise ValueError(f"{name} must contain only strings")
    return tuple(cast(str, item) for item in items)


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    return float(value)


def _optional_number(value: object, name: str) -> float | None:
    if value is None:
        return None
    return _number(value, name)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 date-time string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed
