"""Canonical, backend-neutral observability value types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from ai_multi_agent_platform.contracts.types import JsonValue


def utc_now() -> datetime:
    return datetime.now(UTC)


class TelemetrySeverity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class TelemetryOutcome(StrEnum):
    UNKNOWN = "unknown"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class FailureComponent(StrEnum):
    """Canonical layer responsible for a failure, independent of its implementation."""

    DOMAIN_KERNEL = "domain_kernel"
    ORCHESTRATION = "orchestration"
    AGENT = "agent"
    EXECUTION = "execution"
    MODEL_PROVIDER_ROUTER = "model_provider_router"
    CAPABILITY_TOOL = "capability_tool"
    PERSISTENCE_STORAGE = "persistence_storage"
    AUTHORIZATION_APPROVAL = "authorization_approval"
    VERIFICATION = "verification"
    SCHEDULER_WORKER_NODE = "scheduler_worker_node"
    AUTOMATION = "automation"
    CONNECTOR_BROWSER = "connector_browser"
    PLUGIN_ADAPTER = "plugin_adapter"
    INFRASTRUCTURE_UNKNOWN = "infrastructure_unknown"


class CaptureKind(StrEnum):
    GENERIC = "generic"
    PROMPT = "prompt"
    RESPONSE = "response"
    TOOL_INPUT = "tool_input"
    TOOL_OUTPUT = "tool_output"
    FILE_CONTENT = "file_content"
    AUTH_SESSION = "auth_session"


@dataclass(frozen=True, slots=True)
class TelemetryContext:
    """Identifiers propagated through the canonical trace hierarchy when available."""

    project_id: str | None = None
    workspace_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    agent_id: str | None = None
    team_id: str | None = None
    model_call_id: str | None = None
    model_config_id: str | None = None
    model_provider_id: str | None = None
    tool_invocation_id: str | None = None
    capability_id: str | None = None
    worker_job_id: str | None = None
    node_id: str | None = None
    worker_id: str | None = None
    automation_id: str | None = None
    trigger_id: str | None = None
    approval_id: str | None = None
    verification_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    adapter_id: str | None = None
    provider_id: str | None = None

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank when provided")

    def fields(self) -> dict[str, str]:
        return {
            name: value
            for name in self.__dataclass_fields__
            if (value := getattr(self, name)) is not None
        }


@dataclass(frozen=True, slots=True)
class FailureClassification:
    component: FailureComponent
    code: str
    retryable: bool = False

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("failure code must not be blank")


@dataclass(frozen=True, slots=True)
class StructuredLog:
    severity: TelemetrySeverity
    component: FailureComponent
    event_name: str
    context: TelemetryContext = field(default_factory=TelemetryContext)
    outcome: TelemetryOutcome = TelemetryOutcome.UNKNOWN
    failure: FailureClassification | None = None
    duration_seconds: float | None = None
    attributes: dict[str, JsonValue] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.event_name.strip():
            raise ValueError("event_name must not be blank")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("duration_seconds must not be negative")


@dataclass(frozen=True, slots=True)
class MetricRecord:
    name: str
    value: float
    context: TelemetryContext = field(default_factory=TelemetryContext)
    unit: str = "count"
    attributes: dict[str, JsonValue] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("metric name must not be blank")
        if not self.unit.strip():
            raise ValueError("metric unit must not be blank")


@dataclass(frozen=True, slots=True)
class SpanRecord:
    name: str
    trace_id: str
    span_id: str
    context: TelemetryContext
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    parent_span_id: str | None = None
    outcome: TelemetryOutcome = TelemetryOutcome.UNKNOWN
    failure: FailureClassification | None = None
    attributes: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("name", "trace_id", "span_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        if self.parent_span_id is not None and not self.parent_span_id.strip():
            raise ValueError("parent_span_id must not be blank when provided")
        if self.duration_seconds < 0:
            raise ValueError("duration_seconds must not be negative")
        if self.finished_at < self.started_at:
            raise ValueError("span finished_at cannot precede started_at")


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    event_name: str
    component: FailureComponent
    context: TelemetryContext
    timestamp: datetime = field(default_factory=utc_now)
    outcome: TelemetryOutcome = TelemetryOutcome.UNKNOWN
    duration_seconds: float | None = None
    failure: FailureClassification | None = None
    attributes: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_name.strip():
            raise ValueError("timeline event_name must not be blank")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise ValueError("duration_seconds must not be negative")


@dataclass(frozen=True, slots=True)
class CapturePolicy:
    """Default-deny policy for content-bearing telemetry with recursive secret redaction."""

    capture_prompts: bool = False
    capture_responses: bool = False
    capture_tool_inputs: bool = False
    capture_tool_outputs: bool = False
    capture_file_contents: bool = False
    capture_auth_session_values: bool = False
    replacement: str = "[REDACTED]"
    sensitive_keys: frozenset[str] = frozenset(
        {
            "api_key",
            "apikey",
            "authorization",
            "cookie",
            "credential",
            "credentials",
            "password",
            "secret",
            "session",
            "session_id",
            "access_token",
            "refresh_token",
            "id_token",
        }
    )

    def permits(self, kind: CaptureKind) -> bool:
        if kind is CaptureKind.GENERIC:
            return True
        return {
            CaptureKind.PROMPT: self.capture_prompts,
            CaptureKind.RESPONSE: self.capture_responses,
            CaptureKind.TOOL_INPUT: self.capture_tool_inputs,
            CaptureKind.TOOL_OUTPUT: self.capture_tool_outputs,
            CaptureKind.FILE_CONTENT: self.capture_file_contents,
            CaptureKind.AUTH_SESSION: self.capture_auth_session_values,
        }[kind]

    def redact(
        self,
        values: Mapping[str, JsonValue],
        *,
        kind: CaptureKind = CaptureKind.GENERIC,
    ) -> dict[str, JsonValue]:
        if not self.permits(kind):
            return {"capture": self.replacement}
        return {key: self._redact_value(key, value) for key, value in values.items()}

    def _redact_value(self, key: str, value: JsonValue) -> JsonValue:
        normalized = key.strip().lower().replace("-", "_")
        if normalized in self.sensitive_keys or normalized.endswith("_secret"):
            return self.replacement
        if normalized.endswith("_password") or normalized.endswith("_credential"):
            return self.replacement
        if normalized.endswith("_token") and normalized != "token_count":
            return self.replacement
        if isinstance(value, dict):
            return {child: self._redact_value(child, item) for child, item in value.items()}
        if isinstance(value, list):
            return [self._redact_nested(item) for item in value]
        return value

    def _redact_nested(self, value: JsonValue) -> JsonValue:
        if isinstance(value, dict):
            return {key: self._redact_value(key, item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact_nested(item) for item in value]
        return value
