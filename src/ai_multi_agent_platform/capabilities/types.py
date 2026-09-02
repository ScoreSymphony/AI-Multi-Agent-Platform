"""Canonical tool-capability contracts owned by the platform."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.contracts.types import (
    AdapterMetadata,
    HealthStatus,
    JsonValue,
    OperationContext,
)


def _freeze_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    """Take a shallow immutable snapshot of schema/metadata mappings."""

    return MappingProxyType(dict(value))


class SafetyClassification(StrEnum):
    """Platform-level safety sensitivity for a capability."""

    STANDARD = "standard"
    RESTRICTED = "restricted"
    SENSITIVE = "sensitive"


class SideEffectClassification(StrEnum):
    """Portable side-effect class used by policy/approval hooks."""

    NONE = "none"
    LOCAL_WRITE = "local_write"
    EXTERNAL = "external"
    DESTRUCTIVE = "destructive"


class PolicyDecision(StrEnum):
    """Minimal authorization hook result until issue #15 supplies the final policy engine."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class InvocationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """Backend-neutral definition of one invokable platform capability."""

    capability_id: str
    name: str
    version: str = "1.0"
    description: str = ""
    input_schema: Mapping[str, JsonValue] = field(default_factory=dict)
    output_schema: Mapping[str, JsonValue] | None = None
    tags: tuple[str, ...] = ()
    safety: SafetyClassification = SafetyClassification.STANDARD
    side_effects: SideEffectClassification = SideEffectClassification.NONE
    required_permissions: tuple[str, ...] = ()
    required_approvals: tuple[str, ...] = ()
    required_worker_capabilities: tuple[str, ...] = ()
    timeout_seconds: float | None = None
    health: HealthStatus = HealthStatus.UNKNOWN
    available: bool = True

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("capability_id must not be blank")
        if not self.name.strip():
            raise ValueError("capability name must not be blank")
        if not self.version.strip():
            raise ValueError("capability version must not be blank")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        object.__setattr__(self, "input_schema", _freeze_mapping(self.input_schema))
        if self.output_schema is not None:
            object.__setattr__(self, "output_schema", _freeze_mapping(self.output_schema))


@dataclass(frozen=True, slots=True)
class CapabilityRegistration:
    """Binding from one canonical capability version to one concrete provider tool."""

    capability: CapabilitySpec
    provider_id: str
    provider_tool_ref: str
    priority: int = 0
    node_id: str | None = None
    worker_id: str | None = None
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id must not be blank")
        if not self.provider_tool_ref.strip():
            raise ValueError("provider_tool_ref must not be blank")


@dataclass(frozen=True, slots=True)
class InvocationTrace:
    """Canonical trace links retained at the platform layer."""

    correlation_id: str
    task_id: str
    run_id: str
    agent_id: str
    project_id: str | None = None
    causation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.correlation_id.strip():
            raise ValueError("correlation_id must not be blank")
        if not self.task_id.strip():
            raise ValueError("task_id must not be blank")
        if not self.run_id.strip():
            raise ValueError("run_id must not be blank")
        if not self.agent_id.strip():
            raise ValueError("agent_id must not be blank")


@dataclass(frozen=True, slots=True)
class CapabilityInvocation:
    """Canonical capability request before provider-specific tool resolution."""

    invocation_id: str
    capability_id: str
    arguments: Mapping[str, JsonValue]
    context: OperationContext
    trace: InvocationTrace
    version: str | None = None
    granted_permissions: frozenset[str] = frozenset()
    available_worker_capabilities: frozenset[str] = frozenset()
    approval_grants: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.invocation_id.strip():
            raise ValueError("invocation_id must not be blank")
        if not self.capability_id.strip():
            raise ValueError("capability_id must not be blank")
        if self.version is not None and not self.version.strip():
            raise ValueError("version must not be blank")
        if self.trace.correlation_id != self.context.correlation_id:
            raise ValueError("trace/context correlation_id must match")
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments))


@dataclass(frozen=True, slots=True)
class CapabilityInvocationResult:
    """Normalized invocation result independent from the selected backend."""

    invocation_id: str
    capability_id: str
    capability_version: str
    provider_id: str
    status: InvocationStatus
    output: JsonValue = None
    result_ref: str | None = None
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        if not self.invocation_id.strip():
            raise ValueError("invocation_id must not be blank")
        if not self.capability_id.strip():
            raise ValueError("capability_id must not be blank")
        if not self.capability_version.strip():
            raise ValueError("capability_version must not be blank")
        if not self.provider_id.strip():
            raise ValueError("provider_id must not be blank")


@dataclass(frozen=True, slots=True)
class InvocationRecord:
    """Audit/observability record emitted for every canonical tool invocation."""

    invocation_id: str
    capability_id: str
    capability_version: str
    provider_id: str
    provider_tool_ref: str
    status: InvocationStatus
    trace: InvocationTrace
    error_code: str | None = None
    adapter_metadata: tuple[AdapterMetadata, ...] = ()
