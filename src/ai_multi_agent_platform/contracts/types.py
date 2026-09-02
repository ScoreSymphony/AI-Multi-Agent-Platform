"""Provider-neutral value types used by platform contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

CONTRACT_VERSION = "1.0"


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


@dataclass(frozen=True, slots=True)
class Capability:
    """A discoverable capability advertised by a provider."""

    name: str
    kind: CapabilityKind
    version: str = CONTRACT_VERSION
    attributes: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Stable public metadata about one provider implementation."""

    provider_id: str
    provider_type: str
    contract_version: str = CONTRACT_VERSION
    capabilities: tuple[Capability, ...] = ()


@dataclass(frozen=True, slots=True)
class OperationContext:
    """Trace/ownership metadata propagated across provider boundaries."""

    correlation_id: str
    causation_id: str | None = None
    owner_type: str | None = None
    owner_id: str | None = None
    project_id: str | None = None


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


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    run_id: str
    status: str
    output: dict[str, JsonValue] = field(default_factory=dict)


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


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    invocation_id: str
    tool_ref: str
    arguments: dict[str, JsonValue]
    context: OperationContext


@dataclass(frozen=True, slots=True)
class ToolResult:
    invocation_id: str
    output: JsonValue


@dataclass(frozen=True, slots=True)
class StoredObject:
    object_ref: str
    metadata: dict[str, JsonValue] = field(default_factory=dict)


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


@dataclass(frozen=True, slots=True)
class PlatformEvent:
    event_id: str
    event_type: str
    subject_type: str
    subject_id: str
    context: OperationContext
    payload: dict[str, JsonValue] = field(default_factory=dict)


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


@dataclass(frozen=True, slots=True)
class NodeDescriptor:
    node_id: str
    capabilities: tuple[Capability, ...] = ()
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkerDescriptor:
    worker_id: str
    node_id: str
    capabilities: tuple[Capability, ...] = ()
    available: bool = True
