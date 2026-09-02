"""Provider-neutral value types used by platform contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type FrozenJsonValue = JsonScalar | tuple[FrozenJsonValue, ...] | Mapping[str, FrozenJsonValue]

CONTRACT_VERSION = "1.0"


def _freeze_json(value: JsonValue) -> FrozenJsonValue:
    """Copy a JSON-compatible value into an immutable representation."""

    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: JsonValue | FrozenJsonValue) -> JsonValue:
    """Copy an immutable JSON value back to a standard JSON-serializable value."""

    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_thaw_json(item) for item in value]
    return value


class CapabilityKind(StrEnum):
    """Broad, implementation-neutral capability categories."""

    ORCHESTRATION = "orchestration"
    EXECUTION = "execution"
    MODEL = "model"
    TOOL = "tool"
    MEMORY = "memory"
    FILE = "file"
    KNOWLEDGE = "knowledge"
    EVENT = "event"
    AUTHORIZATION = "authorization"
    NODE = "node"
    WORKER = "worker"


class ExecutionStatus(StrEnum):
    """Normalized execution states matching the canonical Run lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class HealthStatus(StrEnum):
    """Provider health/availability reported through the canonical boundary."""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class RetryMode(StrEnum):
    """Caller intent for retries at a provider boundary."""

    NEVER = "never"
    SAFE = "safe"
    IDEMPOTENT = "idempotent"


@dataclass(frozen=True, slots=True)
class OperationControl:
    """Portable timeout, retry and idempotency expectations for one operation."""

    timeout_seconds: float | None = None
    idempotency_key: str | None = None
    retry_mode: RetryMode = RetryMode.NEVER

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if self.idempotency_key is not None and not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")


@dataclass(frozen=True, slots=True)
class AdapterMetadata:
    """Backend-private diagnostic metadata isolated under an explicit namespace."""

    namespace: str
    values: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.namespace.strip() or any(char.isspace() for char in self.namespace):
            raise ValueError("adapter metadata namespace must be non-empty and contain no spaces")


@dataclass(frozen=True, slots=True)
class Capability:
    """A discoverable capability advertised by a provider."""

    name: str
    kind: CapabilityKind
    version: str = CONTRACT_VERSION
    supported_operations: tuple[str, ...] = ()
    modalities: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    limits: dict[str, JsonValue] = field(default_factory=dict)
    attributes: dict[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Stable public metadata about one provider implementation."""

    provider_id: str
    provider_type: str
    contract_version: str = CONTRACT_VERSION
    supported_operations: tuple[str, ...] = ()
    capabilities: tuple[Capability, ...] = ()
    health: HealthStatus = HealthStatus.UNKNOWN
    available: bool = True
    limits: dict[str, JsonValue] = field(default_factory=dict)
    resources: dict[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class OperationContext:
    """Trace/ownership/control metadata propagated across provider boundaries."""

    correlation_id: str
    causation_id: str | None = None
    owner_type: str | None = None
    owner_id: str | None = None
    project_id: str | None = None
    control: OperationControl = field(default_factory=OperationControl)


@dataclass(frozen=True, slots=True)
class PlanRequest:
    task_id: str
    context: OperationContext
    objective: str


@dataclass(frozen=True, slots=True)
class PlanResponse:
    plan_ref: str
    summary: str
    step_refs: tuple[str, ...] = ()
    adapter_metadata: tuple[AdapterMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    run_id: str
    subject_type: str
    subject_id: str
    context: OperationContext
    input: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExecutionHandle:
    run_id: str
    backend_ref: str | None = None
    adapter_metadata: tuple[AdapterMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    run_id: str
    status: ExecutionStatus
    output: dict[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelRequest:
    request_id: str
    messages: tuple[str, ...]
    context: OperationContext
    requirements: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelResponse:
    request_id: str
    text: str
    model_ref: str
    usage: dict[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    invocation_id: str
    tool_ref: str
    arguments: Mapping[str, JsonValue]
    context: OperationContext

    def __post_init__(self) -> None:
        frozen = MappingProxyType(
            {key: _freeze_json(value) for key, value in dict(self.arguments).items()}
        )
        object.__setattr__(self, "arguments", frozen)

    def arguments_json(self) -> dict[str, JsonValue]:
        """Return a detached, standard JSON-serializable copy for provider transport."""

        return {key: _thaw_json(value) for key, value in self.arguments.items()}


@dataclass(frozen=True, slots=True)
class ToolResult:
    invocation_id: str
    output: JsonValue
    adapter_metadata: tuple[AdapterMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class StoredObject:
    object_ref: str
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class KnowledgeQuery:
    query: str
    context: OperationContext
    filters: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    ref: str
    content: str
    score: float | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class PlatformEvent:
    event_id: str
    event_type: str
    subject_type: str
    subject_id: str
    occurred_at: str
    context: OperationContext
    payload: dict[str, JsonValue] = field(default_factory=dict)
    schema_version: str = CONTRACT_VERSION
    adapter_metadata: tuple[AdapterMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    principal_ref: str
    action: str
    resource_ref: str
    context: OperationContext


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    reason: str | None = None
    adapter_metadata: tuple[AdapterMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class NodeDescriptor:
    node_id: str
    capabilities: tuple[Capability, ...] = ()
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkerDescriptor:
    worker_id: str
    node_id: str
    capabilities: tuple[Capability, ...] = ()
    available: bool = True
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()
