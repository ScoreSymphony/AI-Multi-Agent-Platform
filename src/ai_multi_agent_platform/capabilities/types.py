"""Canonical tool-capability contracts owned by the platform."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.contracts.types import (
    AdapterMetadata,
    HealthStatus,
    JsonValue,
    OperationContext,
)
from ai_multi_agent_platform.domain import validate_id

ISOLATED_WORKSPACE_WRITE_FEATURE = "isolated_workspace_write"


def _utc_now() -> datetime:
    """Return an aware UTC timestamp without expanding the public domain API."""

    return datetime.now(UTC)


def _freeze_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    """Take an immutable snapshot of one top-level JSON mapping."""

    return MappingProxyType(dict(value))


def _validate_compatibility_version(value: str, field_name: str) -> None:
    """Validate the numeric version subset used for canonical compatibility matching."""

    parts = value.split(".")
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        raise ValueError(
            f"{field_name} must be a one-to-three-part dotted numeric version; "
            "use exact version matching for other version identifiers"
        )


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


class CredentialRequirement(StrEnum):
    """Backend-neutral classification for capabilities that require credentials."""

    NONE = "none"
    REQUIRED = "required"


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
class CapabilityCompatibilityRequest:
    """Canonical request for deterministic version/feature compatibility resolution.

    Compatibility ranges intentionally use a small, provider-independent numeric version
    subset. Arbitrary provider version labels remain supported through exact ``version``
    matching, but the platform never guesses ordering/compatibility for those labels.
    """

    minimum_version: str | None = None
    maximum_version: str | None = None
    include_minimum: bool = True
    include_maximum: bool = False
    required_features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.minimum_version is None
            and self.maximum_version is None
            and not self.required_features
        ):
            raise ValueError("compatibility request must contain a version bound or feature")
        if self.minimum_version is not None:
            if not self.minimum_version.strip():
                raise ValueError("minimum_version must not be blank")
            _validate_compatibility_version(self.minimum_version, "minimum_version")
        if self.maximum_version is not None:
            if not self.maximum_version.strip():
                raise ValueError("maximum_version must not be blank")
            _validate_compatibility_version(self.maximum_version, "maximum_version")
        if self.minimum_version is not None and self.maximum_version is not None:
            minimum = _numeric_version_key(self.minimum_version)
            maximum = _numeric_version_key(self.maximum_version)
            if minimum > maximum:
                raise ValueError("minimum_version must not be greater than maximum_version")
        if any(not feature.strip() for feature in self.required_features):
            raise ValueError("required_features must not contain blank values")
        if len(set(self.required_features)) != len(self.required_features):
            raise ValueError("required_features must not contain duplicates")


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
    health: HealthStatus = field(default=HealthStatus.UNKNOWN, compare=False)
    available: bool = field(default=True, compare=False)
    features: tuple[str, ...] = ()
    credential_requirement: CredentialRequirement = CredentialRequirement.NONE

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("capability_id must not be blank")
        if not self.name.strip():
            raise ValueError("capability name must not be blank")
        if not self.version.strip():
            raise ValueError("capability version must not be blank")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if any(not feature.strip() for feature in self.features):
            raise ValueError("features must not contain blank values")
        if len(set(self.features)) != len(self.features):
            raise ValueError("features must not contain duplicates")
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
        if self.node_id is not None:
            validate_id(self.node_id, "node")
        if self.worker_id is not None:
            validate_id(self.worker_id, "worker")


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
        validate_id(self.task_id, "task")
        validate_id(self.run_id, "run")
        validate_id(self.agent_id, "agent")
        if self.project_id is not None:
            validate_id(self.project_id, "project")


@dataclass(frozen=True, slots=True)
class CapabilityDiscoveryRequest:
    """Canonical caller/scope context used for policy-aware capability discovery."""

    context: OperationContext
    granted_permissions: frozenset[str] = frozenset()
    available_worker_capabilities: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class CapabilityInvocation:
    """Canonical capability request before provider-specific tool resolution."""

    invocation_id: str
    capability_id: str
    arguments: Mapping[str, JsonValue]
    context: OperationContext
    trace: InvocationTrace
    version: str | None = None
    compatibility: CapabilityCompatibilityRequest | None = None
    granted_permissions: frozenset[str] = frozenset()
    available_worker_capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.invocation_id.strip():
            raise ValueError("invocation_id must not be blank")
        if not self.capability_id.strip():
            raise ValueError("capability_id must not be blank")
        if self.version is not None and not self.version.strip():
            raise ValueError("version must not be blank")
        if self.version is not None and self.compatibility is not None:
            raise ValueError("version and compatibility are mutually exclusive")
        if self.trace.correlation_id != self.context.correlation_id:
            raise ValueError("trace/context correlation_id must match")
        if self.trace.causation_id != self.context.causation_id:
            raise ValueError("trace/context causation_id must match")
        if self.trace.project_id != self.context.project_id:
            raise ValueError("trace/context project_id must match")
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
    canonical_tool_invocation_id: str | None = None
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
        if self.canonical_tool_invocation_id is not None:
            validate_id(self.canonical_tool_invocation_id, "tool_invocation")


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
    recorded_at: datetime = field(default_factory=_utc_now)
    canonical_tool_invocation_id: str | None = None
    node_id: str | None = None
    worker_id: str | None = None
    approval_decision: str | None = None
    error_code: str | None = None
    adapter_metadata: tuple[AdapterMetadata, ...] = ()

    def __post_init__(self) -> None:
        if self.canonical_tool_invocation_id is not None:
            validate_id(self.canonical_tool_invocation_id, "tool_invocation")
        if self.node_id is not None:
            validate_id(self.node_id, "node")
        if self.worker_id is not None:
            validate_id(self.worker_id, "worker")
        if self.approval_decision is not None and not self.approval_decision.strip():
            raise ValueError("approval_decision must not be blank")


def _numeric_version_key(version: str) -> tuple[int, int, int]:
    """Normalize a validated one-to-three-part dotted numeric version."""

    values = [int(part) for part in version.split(".")]
    values.extend([0] * (3 - len(values)))
    return values[0], values[1], values[2]
