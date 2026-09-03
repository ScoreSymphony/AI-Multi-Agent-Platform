"""Transport-neutral trace propagation helpers for worker/message boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .exporters import SpanHandle
from .models import TelemetryContext

_PREFIX = "ai-observability-"
_OPTIONAL_CONTEXT_FIELDS = (
    "causation_id",
    "project_id",
    "workspace_id",
    "task_id",
    "run_id",
    "step_id",
    "agent_id",
    "team_id",
    "model_call_id",
    "model_config_id",
    "model_provider_id",
    "tool_invocation_id",
    "capability_id",
    "worker_job_id",
    "node_id",
    "worker_id",
    "automation_id",
    "trigger_id",
    "approval_id",
    "verification_id",
    "adapter_id",
    "provider_id",
)


@dataclass(frozen=True, slots=True)
class TraceCarrier:
    """Backend-neutral trace parent plus canonical context safe to cross process boundaries."""

    trace_id: str
    parent_span_id: str
    correlation_id: str
    causation_id: str | None = None
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
    adapter_id: str | None = None
    provider_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("trace_id", "parent_span_id", "correlation_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        for name in _OPTIONAL_CONTEXT_FIELDS:
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank when provided")

    @classmethod
    def from_span(cls, span: SpanHandle) -> TraceCarrier:
        if span.context.correlation_id is None:
            raise ValueError("span context must include correlation_id for propagation")
        context = span.context
        return cls(
            trace_id=span.trace_id,
            parent_span_id=span.span_id,
            correlation_id=context.correlation_id,
            **{
                name: getattr(context, name)
                for name in _OPTIONAL_CONTEXT_FIELDS
                if name != "causation_id" or getattr(context, name) is not None
            },
        )

    def to_mapping(self) -> dict[str, str]:
        values: dict[str, str | None] = {
            "trace-id": self.trace_id,
            "parent-span-id": self.parent_span_id,
            "correlation-id": self.correlation_id,
        }
        values.update(
            {
                name.replace("_", "-"): getattr(self, name)
                for name in _OPTIONAL_CONTEXT_FIELDS
            }
        )
        return {f"{_PREFIX}{key}": value for key, value in values.items() if value is not None}

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> TraceCarrier:
        def required(name: str) -> str:
            value = values.get(f"{_PREFIX}{name}")
            if value is None or not value.strip():
                raise ValueError(f"missing trace propagation field: {name}")
            return value

        def optional(name: str) -> str | None:
            value = values.get(f"{_PREFIX}{name}")
            if value is None:
                return None
            if not value.strip():
                raise ValueError(f"trace propagation field must not be blank: {name}")
            return value

        kwargs = {
            name: optional(name.replace("_", "-"))
            for name in _OPTIONAL_CONTEXT_FIELDS
        }
        return cls(
            trace_id=required("trace-id"),
            parent_span_id=required("parent-span-id"),
            correlation_id=required("correlation-id"),
            **kwargs,
        )

    def child_context(self) -> TelemetryContext:
        return TelemetryContext(
            correlation_id=self.correlation_id,
            **{name: getattr(self, name) for name in _OPTIONAL_CONTEXT_FIELDS},
        )
