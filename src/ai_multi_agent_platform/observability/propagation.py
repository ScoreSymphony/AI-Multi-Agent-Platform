"""Transport-neutral trace propagation helpers for future worker/message boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .exporters import SpanHandle
from .models import TelemetryContext

_PREFIX = "ai-observability-"


@dataclass(frozen=True, slots=True)
class TraceCarrier:
    trace_id: str
    parent_span_id: str
    correlation_id: str
    causation_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    step_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("trace_id", "parent_span_id", "correlation_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        for name in ("causation_id", "project_id", "task_id", "run_id", "step_id"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank when provided")

    @classmethod
    def from_span(cls, span: SpanHandle) -> TraceCarrier:
        if span.context.correlation_id is None:
            raise ValueError("span context must include correlation_id for propagation")
        return cls(
            trace_id=span.trace_id,
            parent_span_id=span.span_id,
            correlation_id=span.context.correlation_id,
            causation_id=span.context.causation_id,
            project_id=span.context.project_id,
            task_id=span.context.task_id,
            run_id=span.context.run_id,
            step_id=span.context.step_id,
        )

    def to_mapping(self) -> dict[str, str]:
        values = {
            "trace-id": self.trace_id,
            "parent-span-id": self.parent_span_id,
            "correlation-id": self.correlation_id,
            "causation-id": self.causation_id,
            "project-id": self.project_id,
            "task-id": self.task_id,
            "run-id": self.run_id,
            "step-id": self.step_id,
        }
        return {
            f"{_PREFIX}{key}": value
            for key, value in values.items()
            if value is not None
        }

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

        return cls(
            trace_id=required("trace-id"),
            parent_span_id=required("parent-span-id"),
            correlation_id=required("correlation-id"),
            causation_id=optional("causation-id"),
            project_id=optional("project-id"),
            task_id=optional("task-id"),
            run_id=optional("run-id"),
            step_id=optional("step-id"),
        )

    def child_context(self) -> TelemetryContext:
        return TelemetryContext(
            project_id=self.project_id,
            task_id=self.task_id,
            run_id=self.run_id,
            step_id=self.step_id,
            correlation_id=self.correlation_id,
            causation_id=self.causation_id,
        )
