"""Provider-neutral value types used by platform contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from ai_multi_agent_platform.domain import Event, RunStatus, validate_id, validate_subject_id

ExecutionStatus = RunStatus
PlatformEvent = Event

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type FrozenJsonValue = JsonScalar | tuple[FrozenJsonValue, ...] | Mapping[str, FrozenJsonValue]

CONTRACT_VERSION = "2.0"


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
    """A discoverable provider capability, distinct from persisted domain capability state."""

    name: str
    kind: CapabilityKind
    version: str = CONTRACT_VERSION
    supported_operations: tuple[str, ...] = ()
    modalities: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    limits: dict[str, JsonValue] = field(default_factory=dict)
    attributes: dict[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("capability name must not be blank")
        if not self.version.strip():
            raise ValueError("capability version must not be blank")


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

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id must not be blank")
        if not self.provider_type.strip():
            raise ValueError("provider_type must not be blank")
        if not self.contract_version.strip():
            raise ValueError("contract_version must not be blank")


@dataclass(frozen=True, slots=True)
class OperationContext:
    """Trace/ownership/control metadata propagated across provider boundaries."""

    correlation_id: str
    causation_id: str | None = None
    owner_type: str | None = None
    owner_id: str | None = None
    project_id: str | None = None
    control: OperationControl = field(default_factory=OperationControl)

    def __post_init__(self) -> None:
        if not self.correlation_id.strip():
            raise ValueError("correlation_id must not be blank")
        if (self.owner_type is None) != (self.owner_id is None):
            raise ValueError("owner_type and owner_id must either both be set or both be omitted")
        if self.project_id is not None:
            validate_id(self.project_id, "project")


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """Planning request for one already-canonical Task."""

    task_id: str
    context: OperationContext
    objective: str

    def __post_init__(self) -> None:
        validate_id(self.task_id, "task")
        if not self.objective.strip():
            raise ValueError("planning objective must not be blank")


@dataclass(frozen=True, slots=True)
class PlanStepProposal:
    """Proposal-local step description without canonical Plan/Step identity."""

    key: str
    title: str
    objective: str = ""
    depends_on: tuple[str, ...] = ()
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("plan step proposal key must not be blank")
        if not self.title.strip():
            raise ValueError("plan step proposal title must not be blank")
        if self.key in self.depends_on:
            raise ValueError("plan step proposal cannot depend on itself")


@dataclass(frozen=True, slots=True)
class PlanResponse:
    """Provider-neutral plan proposal; canonical Plan/Step IDs remain platform-owned."""

    summary: str
    steps: tuple[PlanStepProposal, ...] = ()
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("plan summary must not be blank")
        keys = [step.key for step in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError("plan step proposal keys must be unique")
        known = set(keys)
        for step in self.steps:
            unknown = set(step.depends_on) - known
            if unknown:
                raise ValueError(
                    f"plan step proposal has unknown dependencies: {sorted(unknown)!r}"
                )


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """Execution request for one canonical Run attempt."""

    run_id: str
    subject_type: Literal["task", "step"]
    subject_id: str
    context: OperationContext
    input: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.run_id, "run")
        validate_subject_id(self.subject_type, self.subject_id)


@dataclass(frozen=True, slots=True)
class ExecutionHandle:
    run_id: str
    backend_ref: str | None = None
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.run_id, "run")


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    run_id: str
    status: ExecutionStatus
    output: dict[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.run_id, "run")


@dataclass(frozen=True, slots=True)
class ModelRequest:
    request_id: str
    messages: tuple[str, ...]
    context: OperationContext
    requirements: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("model request_id must not be blank")
        if not self.messages:
            raise ValueError("model request must contain at least one message")


@dataclass(frozen=True, slots=True)
class ModelResponse:
    request_id: str
    text: str
    model_ref: str
    usage: dict[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("model response request_id must not be blank")
        if not self.model_ref.strip():
            raise ValueError("model_ref must not be blank")


@dataclass(frozen=True, slots=True)
class ModelSelection:
    """Typed model-router result without exposing provider SDK objects."""

    provider_id: str
    model_ref: str | None = None
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("selected provider_id must not be blank")
        if self.model_ref is not None and not self.model_ref.strip():
            raise ValueError("selected model_ref must not be blank")


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    invocation_id: str
    tool_ref: str
    arguments: Mapping[str, JsonValue]
    context: OperationContext
    task_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None

    def __post_init__(self) -> None:
        if not self.invocation_id.strip():
            raise ValueError("tool invocation_id must not be blank")
        if not self.tool_ref.strip():
            raise ValueError("tool_ref must not be blank")
        if self.task_id is not None:
            validate_id(self.task_id, "task")
        if self.run_id is not None:
            validate_id(self.run_id, "run")
        if self.agent_id is not None:
            validate_id(self.agent_id, "agent")
        frozen = MappingProxyType(
            {key: _freeze_json(value) for key, value in dict(self.arguments).items()}
        )
        object.__setattr__(self, "arguments", frozen)

    def arguments_json(self) -> dict[str, JsonValue]:
        """Return a detached standard JSON representation for provider transport."""

        return {key: _thaw_json(value) for key, value in self.arguments.items()}


@dataclass(frozen=True, slots=True)
class ToolResult:
    invocation_id: str
    output: JsonValue
    adapter_metadata: tuple[AdapterMetadata, ...] = ()
    result_ref: str | None = None
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.invocation_id.strip():
            raise ValueError("tool result invocation_id must not be blank")
        if self.result_ref is not None and not self.result_ref.strip():
            raise ValueError("tool result_ref must not be blank")
        if any(not ref.strip() for ref in self.artifact_refs):
            raise ValueError("tool artifact_refs must not contain blank references")
        if any(not ref.strip() for ref in self.evidence_refs):
            raise ValueError("tool evidence_refs must not contain blank references")


@dataclass(frozen=True, slots=True)
class StoredObject:
    object_ref: str
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        if not self.object_ref.strip():
            raise ValueError("object_ref must not be blank")


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

    def __post_init__(self) -> None:
        if not self.ref.strip():
            raise ValueError("knowledge ref must not be blank")


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    principal_ref: str
    action: str
    resource_ref: str
    context: OperationContext

    def __post_init__(self) -> None:
        if not self.principal_ref.strip():
            raise ValueError("principal_ref must not be blank")
        if not self.action.strip():
            raise ValueError("authorization action must not be blank")
        if not self.resource_ref.strip():
            raise ValueError("authorization resource_ref must not be blank")


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

    def __post_init__(self) -> None:
        validate_id(self.node_id, "node")


@dataclass(frozen=True, slots=True)
class WorkerDescriptor:
    worker_id: str
    node_id: str
    capabilities: tuple[Capability, ...] = ()
    available: bool = True
    metadata: dict[str, JsonValue] = field(default_factory=dict)
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.worker_id, "worker")
        validate_id(self.node_id, "node")
