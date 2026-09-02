"""Shared canonical trace hierarchy for progressive platform instrumentation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import replace
from typing import TypeVar

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.contracts.types import JsonValue

from .exporters import SpanHandle, Telemetry
from .models import (
    FailureClassification,
    FailureComponent,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
    utc_now,
)
from .propagation import TraceCarrier

T = TypeVar("T")


class TraceHierarchy:
    """Own parent/child span composition without owning any platform lifecycle state.

    The hierarchy prefers the currently active child span for nested operations,
    then falls back to canonical Run and Task anchors created by the foundation
    instrumentation. This lets later Agent, Model, Tool and Worker domains attach
    telemetry without changing Task/Run ownership.
    """

    def __init__(self, telemetry: Telemetry) -> None:
        self.telemetry = telemetry
        self._active: ContextVar[SpanHandle | None] = ContextVar(
            f"observability-active-span-{id(self)}",
            default=None,
        )

    def current_span(self) -> SpanHandle | None:
        return self._active.get()

    def current_carrier(self) -> TraceCarrier:
        span = self.current_span()
        if span is None:
            raise ValueError("no active span is available for trace propagation")
        return TraceCarrier.from_span(span)

    def parent_for(self, context: TelemetryContext) -> SpanHandle | None:
        active = self.current_span()
        if active is not None and self._same_trace_context(active.context, context):
            return active
        if context.run_id is not None:
            run = self.telemetry.get_anchor("run", context.run_id)
            if run is not None:
                return run
        if context.task_id is not None:
            task = self.telemetry.get_anchor("task", context.task_id)
            if task is not None:
                return task
        if context.correlation_id is not None:
            return self.telemetry.get_anchor("task", context.correlation_id)
        return None

    async def observe(
        self,
        *,
        span_name: str,
        metric_prefix: str,
        event_prefix: str,
        component: FailureComponent,
        context: TelemetryContext,
        operation: Callable[[], Awaitable[T]],
        attributes: dict[str, JsonValue] | None = None,
    ) -> T:
        return await self._observe(
            span_name=span_name,
            metric_prefix=metric_prefix,
            event_prefix=event_prefix,
            component=component,
            context=context,
            operation=operation,
            attributes=attributes,
            parent_override=None,
        )

    async def observe_remote(
        self,
        *,
        carrier: TraceCarrier,
        span_name: str,
        metric_prefix: str,
        event_prefix: str,
        component: FailureComponent,
        context: TelemetryContext,
        operation: Callable[[], Awaitable[T]],
        attributes: dict[str, JsonValue] | None = None,
    ) -> T:
        """Create a child span from a trace carrier reconstructed remotely."""

        remote_parent = SpanHandle(
            name="remote.parent",
            trace_id=carrier.trace_id,
            span_id=carrier.parent_span_id,
            context=carrier.child_context(),
            started_at=utc_now(),
        )
        return await self._observe(
            span_name=span_name,
            metric_prefix=metric_prefix,
            event_prefix=event_prefix,
            component=component,
            context=context,
            operation=operation,
            attributes=attributes,
            parent_override=remote_parent,
        )

    async def _observe(
        self,
        *,
        span_name: str,
        metric_prefix: str,
        event_prefix: str,
        component: FailureComponent,
        context: TelemetryContext,
        operation: Callable[[], Awaitable[T]],
        attributes: dict[str, JsonValue] | None,
        parent_override: SpanHandle | None,
    ) -> T:
        parent = parent_override or self.parent_for(context)
        effective_context = self._inherit_context(context, parent.context if parent else None)
        span = self.telemetry.start_span(span_name, context=effective_context, parent=parent)
        token = self._active.set(span)
        safe_attributes = dict(attributes or {})
        self.telemetry.metric(f"{metric_prefix}.calls", 1.0, context=effective_context)
        self.telemetry.log(
            severity=TelemetrySeverity.INFO,
            component=component,
            event_name=f"{event_prefix}.started",
            context=effective_context,
            attributes=safe_attributes,
        )
        try:
            result = await operation()
        except asyncio.CancelledError:
            failure = FailureClassification(component=component, code="cancelled", retryable=True)
            self._finish_failure(
                span,
                effective_context,
                metric_prefix,
                event_prefix,
                component,
                TelemetryOutcome.CANCELLED,
                failure,
                safe_attributes,
            )
            raise
        except TimeoutError:
            failure = FailureClassification(component=component, code="timeout", retryable=True)
            self._finish_failure(
                span,
                effective_context,
                metric_prefix,
                event_prefix,
                component,
                TelemetryOutcome.TIMED_OUT,
                failure,
                safe_attributes,
            )
            raise
        except Exception as exc:
            failure = self.failure_from_exception(exc, component)
            self._finish_failure(
                span,
                effective_context,
                metric_prefix,
                event_prefix,
                component,
                TelemetryOutcome.FAILED,
                failure,
                safe_attributes,
            )
            raise
        else:
            finished = self.telemetry.finish_span(span, outcome=TelemetryOutcome.SUCCEEDED)
            self.telemetry.metric(
                f"{metric_prefix}.duration_seconds",
                finished.duration_seconds,
                context=effective_context,
                unit="seconds",
            )
            self.telemetry.log(
                severity=TelemetrySeverity.INFO,
                component=component,
                event_name=f"{event_prefix}.completed",
                context=effective_context,
                outcome=TelemetryOutcome.SUCCEEDED,
                duration_seconds=finished.duration_seconds,
                attributes=safe_attributes,
            )
            self.telemetry.timeline(
                event_name=f"{event_prefix}.completed",
                component=component,
                context=effective_context,
                outcome=TelemetryOutcome.SUCCEEDED,
                duration_seconds=finished.duration_seconds,
                attributes=safe_attributes,
            )
            return result
        finally:
            self._active.reset(token)

    def _finish_failure(
        self,
        span: SpanHandle,
        context: TelemetryContext,
        metric_prefix: str,
        event_prefix: str,
        component: FailureComponent,
        outcome: TelemetryOutcome,
        failure: FailureClassification,
        attributes: dict[str, JsonValue],
    ) -> None:
        finished = self.telemetry.finish_span(span, outcome=outcome, failure=failure)
        self.telemetry.metric(f"{metric_prefix}.failures", 1.0, context=context)
        self.telemetry.metric(
            f"{metric_prefix}.duration_seconds",
            finished.duration_seconds,
            context=context,
            unit="seconds",
            attributes={"outcome": outcome.value},
        )
        self.telemetry.log(
            severity=TelemetrySeverity.ERROR,
            component=component,
            event_name=f"{event_prefix}.failed",
            context=context,
            outcome=outcome,
            failure=failure,
            duration_seconds=finished.duration_seconds,
            attributes=attributes,
        )
        self.telemetry.timeline(
            event_name=f"{event_prefix}.failed",
            component=component,
            context=context,
            outcome=outcome,
            failure=failure,
            duration_seconds=finished.duration_seconds,
            attributes=attributes,
        )

    @staticmethod
    def failure_from_exception(
        exc: Exception,
        component: FailureComponent,
    ) -> FailureClassification:
        if isinstance(exc, ContractError):
            return FailureClassification(
                component=component,
                code=exc.code.value,
                retryable=exc.retryable,
            )
        return FailureClassification(
            component=component,
            code=type(exc).__name__,
            retryable=False,
        )

    @staticmethod
    def _same_trace_context(parent: TelemetryContext, child: TelemetryContext) -> bool:
        if child.correlation_id is None or parent.correlation_id is None:
            return True
        return child.correlation_id == parent.correlation_id

    @staticmethod
    def _inherit_context(
        context: TelemetryContext,
        parent: TelemetryContext | None,
    ) -> TelemetryContext:
        if parent is None:
            return context
        return replace(
            context,
            project_id=context.project_id or parent.project_id,
            workspace_id=context.workspace_id or parent.workspace_id,
            task_id=context.task_id or parent.task_id,
            run_id=context.run_id or parent.run_id,
            step_id=context.step_id or parent.step_id,
            agent_id=context.agent_id or parent.agent_id,
            team_id=context.team_id or parent.team_id,
            model_config_id=context.model_config_id or parent.model_config_id,
            capability_id=context.capability_id or parent.capability_id,
            worker_job_id=context.worker_job_id or parent.worker_job_id,
            node_id=context.node_id or parent.node_id,
            worker_id=context.worker_id or parent.worker_id,
            automation_id=context.automation_id or parent.automation_id,
            trigger_id=context.trigger_id or parent.trigger_id,
            approval_id=context.approval_id or parent.approval_id,
            verification_id=context.verification_id or parent.verification_id,
            correlation_id=context.correlation_id or parent.correlation_id,
            causation_id=context.causation_id or parent.causation_id,
        )


async def observe_agent_run[T](
    hierarchy: TraceHierarchy,
    *,
    agent_id: str,
    context: TelemetryContext,
    operation: Callable[[], Awaitable[T]],
    attributes: dict[str, JsonValue] | None = None,
) -> T:
    """Attach a future Agent runtime to the canonical trace without defining Agent state."""

    if not agent_id.strip():
        raise ValueError("agent_id must not be blank")
    return await hierarchy.observe(
        span_name="agent.run",
        metric_prefix="platform.agent",
        event_prefix="agent",
        component=FailureComponent.AGENT,
        context=replace(context, agent_id=agent_id),
        operation=operation,
        attributes=attributes,
    )
