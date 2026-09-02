"""Instrumentation adapters for canonical events and replaceable providers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from threading import Lock

from ai_multi_agent_platform.contracts import (
    ContractError,
    EventProvider,
    HealthStatus,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    OperationControl,
    PlatformEvent,
    ProviderDescriptor,
    ToolInvocation,
    ToolProvider,
    ToolResult,
)
from ai_multi_agent_platform.execution import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    Executor,
    ExecutorDescriptor,
)

from .exporters import SpanHandle, Telemetry
from .models import (
    FailureClassification,
    FailureComponent,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
)

_TERMINAL_EVENT_OUTCOMES: dict[str, TelemetryOutcome] = {
    "task.succeeded": TelemetryOutcome.SUCCEEDED,
    "task.failed": TelemetryOutcome.FAILED,
    "task.cancelled": TelemetryOutcome.CANCELLED,
    "run.succeeded": TelemetryOutcome.SUCCEEDED,
    "run.failed": TelemetryOutcome.FAILED,
    "run.cancelled": TelemetryOutcome.CANCELLED,
    "run.timed_out": TelemetryOutcome.TIMED_OUT,
}

_EXECUTION_OUTCOMES: dict[ExecutionStatus, TelemetryOutcome] = {
    ExecutionStatus.SUCCEEDED: TelemetryOutcome.SUCCEEDED,
    ExecutionStatus.FAILED: TelemetryOutcome.FAILED,
    ExecutionStatus.CANCELLED: TelemetryOutcome.CANCELLED,
    ExecutionStatus.TIMED_OUT: TelemetryOutcome.TIMED_OUT,
}


class ObservabilityEventProvider(EventProvider):
    """Mirror canonical domain events into structured logs, metrics, spans and timeline views."""

    def __init__(self, telemetry: Telemetry) -> None:
        self._telemetry = telemetry
        self._events: list[PlatformEvent] = []
        self._event_ids: set[str] = set()
        self._lock = Lock()

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="observability-events",
            provider_type="event",
            supported_operations=("publish", "read", "subscribe"),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def publish(self, event: PlatformEvent) -> None:
        with self._lock:
            if event.id in self._event_ids:
                return
            self._event_ids.add(event.id)
            self._events.append(event)
        self._instrument(event)

    async def read(
        self,
        correlation_id: str,
        *,
        after_event_id: str | None = None,
        control: OperationControl | None = None,
    ) -> tuple[PlatformEvent, ...]:
        del control
        with self._lock:
            events = tuple(
                event for event in self._events if event.correlation_id == correlation_id
            )
        if after_event_id is None:
            return events
        for index, event in enumerate(events):
            if event.id == after_event_id:
                return events[index + 1 :]
        return ()

    def subscribe(
        self,
        correlation_id: str,
        *,
        after_event_id: str | None = None,
        control: OperationControl | None = None,
    ) -> AsyncIterator[PlatformEvent]:
        async def iterator() -> AsyncIterator[PlatformEvent]:
            events = await self.read(
                correlation_id,
                after_event_id=after_event_id,
                control=control,
            )
            for event in events:
                yield event

        return iterator()

    def _instrument(self, event: PlatformEvent) -> None:
        context = self._event_context(event)
        outcome = _TERMINAL_EVENT_OUTCOMES.get(event.event_type, TelemetryOutcome.UNKNOWN)
        failure = self._event_failure(event.event_type)
        severity = (
            TelemetrySeverity.ERROR
            if outcome in {TelemetryOutcome.FAILED, TelemetryOutcome.TIMED_OUT}
            else TelemetrySeverity.INFO
        )
        duration = self._observe_span_lifecycle(event, context, outcome, failure)

        self._telemetry.log(
            severity=severity,
            component=FailureComponent.DOMAIN_KERNEL,
            event_name=event.event_type,
            context=context,
            outcome=outcome,
            failure=failure,
            duration_seconds=duration,
            attributes={
                "subject_type": event.subject_type,
                "source": event.provenance.source if event.provenance is not None else "unknown",
            },
        )
        self._telemetry.metric(
            "platform.lifecycle.events",
            1.0,
            context=context,
            attributes={"event_type": event.event_type, "subject_type": event.subject_type},
        )
        self._telemetry.timeline(
            event_name=event.event_type,
            component=FailureComponent.DOMAIN_KERNEL,
            context=context,
            timestamp=event.occurred_at,
            outcome=outcome,
            duration_seconds=duration,
            failure=failure,
            attributes={"subject_type": event.subject_type},
        )

    def _observe_span_lifecycle(
        self,
        event: PlatformEvent,
        context: TelemetryContext,
        outcome: TelemetryOutcome,
        failure: FailureClassification | None,
    ) -> float | None:
        if event.event_type == "task.created" and context.task_id is not None:
            self._ensure_task_anchor(context, event.trace_id)
            self._telemetry.metric("platform.tasks.created", 1.0, context=context)
            return None

        if event.event_type == "run.created" and context.run_id is not None:
            task_parent = self._ensure_task_anchor(context, event.trace_id)
            run_anchor = self._telemetry.start_span(
                "run.lifecycle",
                context=context,
                parent=task_parent,
                trace_id=None if task_parent is not None else event.trace_id,
            )
            self._telemetry.set_anchor("run", context.run_id, run_anchor)
            self._telemetry.metric("platform.runs.created", 1.0, context=context)
            return None

        if event.event_type == "run.starting" and context.run_id is not None:
            anchor = self._telemetry.get_anchor("run", context.run_id)
            if anchor is not None:
                queue_wait = max(0.0, (event.occurred_at - anchor.started_at).total_seconds())
                self._telemetry.metric(
                    "platform.run.queue_wait_seconds",
                    queue_wait,
                    context=context,
                    unit="seconds",
                )
                return queue_wait

        if (
            event.event_type
            in {
                "run.succeeded",
                "run.failed",
                "run.cancelled",
                "run.timed_out",
            }
            and context.run_id is not None
        ):
            anchor = self._telemetry.pop_anchor("run", context.run_id)
            if anchor is not None:
                span = self._telemetry.finish_span(anchor, outcome=outcome, failure=failure)
                self._telemetry.metric(
                    "platform.run.duration_seconds",
                    span.duration_seconds,
                    context=context,
                    unit="seconds",
                    attributes={"outcome": outcome.value},
                )
                self._telemetry.metric(
                    "platform.runs.terminal",
                    1.0,
                    context=context,
                    attributes={"outcome": outcome.value},
                )
                return span.duration_seconds

        if event.event_type in {"task.succeeded", "task.failed", "task.cancelled"}:
            if context.task_id is not None:
                anchor = self._telemetry.pop_anchor("task", context.task_id)
                if anchor is not None:
                    span = self._telemetry.finish_span(anchor, outcome=outcome, failure=failure)
                    self._telemetry.metric(
                        "platform.task.duration_seconds",
                        span.duration_seconds,
                        context=context,
                        unit="seconds",
                        attributes={"outcome": outcome.value},
                    )
                    self._telemetry.metric(
                        "platform.tasks.terminal",
                        1.0,
                        context=context,
                        attributes={"outcome": outcome.value},
                    )
                    return span.duration_seconds
        return None

    def _ensure_task_anchor(
        self,
        context: TelemetryContext,
        trace_id: str | None,
    ) -> SpanHandle | None:
        if context.task_id is None:
            return None
        existing = self._telemetry.get_anchor("task", context.task_id)
        if existing is not None:
            return existing
        handle = self._telemetry.start_span(
            "task.lifecycle",
            context=TelemetryContext(
                project_id=context.project_id,
                task_id=context.task_id,
                correlation_id=context.correlation_id,
                causation_id=context.causation_id,
            ),
            trace_id=trace_id,
        )
        self._telemetry.set_anchor("task", context.task_id, handle)
        return handle

    @staticmethod
    def _event_context(event: PlatformEvent) -> TelemetryContext:
        task_id: str | None = None
        run_id: str | None = None
        step_id: str | None = None
        if event.subject_type == "task":
            task_id = event.subject_id
        elif event.subject_type == "run":
            run_id = event.subject_id
            payload_task_id = event.payload.get("task_id")
            if isinstance(payload_task_id, str):
                task_id = payload_task_id
            elif event.correlation_id.startswith("task_"):
                task_id = event.correlation_id
        elif event.subject_type == "step":
            step_id = event.subject_id
            if event.correlation_id.startswith("task_"):
                task_id = event.correlation_id
        elif event.correlation_id.startswith("task_"):
            task_id = event.correlation_id

        return TelemetryContext(
            project_id=event.project_id,
            task_id=task_id,
            run_id=run_id,
            step_id=step_id,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
        )

    @staticmethod
    def _event_failure(event_type: str) -> FailureClassification | None:
        if event_type in {"task.failed", "run.failed", "run.timed_out"}:
            return FailureClassification(
                component=FailureComponent.DOMAIN_KERNEL,
                code=event_type,
                retryable=False,
            )
        return None


class ObservedExecutor(Executor):
    """Instrument any Executor without changing its canonical execution contract."""

    def __init__(self, executor: Executor, telemetry: Telemetry) -> None:
        self._executor = executor
        self._telemetry = telemetry

    @property
    def descriptor(self) -> ExecutorDescriptor:
        return self._executor.descriptor

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        context = TelemetryContext(
            task_id=request.task_id,
            run_id=request.run_id,
            step_id=request.step_id,
            correlation_id=request.correlation_id,
            adapter_id=self.descriptor.executor_id,
            provider_id=self.descriptor.executor_id,
        )
        parent = self._telemetry.get_anchor("run", request.run_id)
        if parent is None:
            parent = self._telemetry.get_anchor("task", request.task_id)
        span = self._telemetry.start_span("executor.execute", context=context, parent=parent)
        self._telemetry.metric("platform.executor.calls", 1.0, context=context)
        self._telemetry.log(
            severity=TelemetrySeverity.INFO,
            component=FailureComponent.EXECUTION,
            event_name="executor.started",
            context=context,
            attributes={"action": request.action, "executor_id": self.descriptor.executor_id},
        )
        try:
            result = await self._executor.execute(request)
        except Exception as exc:
            failure = self._failure_from_exception(exc, FailureComponent.EXECUTION)
            finished = self._telemetry.finish_span(
                span,
                outcome=TelemetryOutcome.FAILED,
                failure=failure,
            )
            self._telemetry.metric(
                "platform.executor.duration_seconds",
                finished.duration_seconds,
                context=context,
                unit="seconds",
                attributes={"outcome": TelemetryOutcome.FAILED.value},
            )
            self._telemetry.metric("platform.executor.failures", 1.0, context=context)
            self._telemetry.log(
                severity=TelemetrySeverity.ERROR,
                component=FailureComponent.EXECUTION,
                event_name="executor.failed",
                context=context,
                outcome=TelemetryOutcome.FAILED,
                failure=failure,
                duration_seconds=finished.duration_seconds,
            )
            self._telemetry.timeline(
                event_name="executor.failed",
                component=FailureComponent.EXECUTION,
                context=context,
                outcome=TelemetryOutcome.FAILED,
                failure=failure,
                duration_seconds=finished.duration_seconds,
            )
            raise

        outcome = _EXECUTION_OUTCOMES[result.status]
        failure = None
        if result.error is not None:
            failure = FailureClassification(
                component=FailureComponent.EXECUTION,
                code=result.error.category.value,
                retryable=result.error.retryable,
            )
        finished = self._telemetry.finish_span(span, outcome=outcome, failure=failure)
        duration = result.duration_seconds
        if duration is None:
            duration = finished.duration_seconds
        self._telemetry.metric(
            "platform.executor.duration_seconds",
            duration,
            context=context,
            unit="seconds",
            attributes={"outcome": outcome.value},
        )
        if outcome in {TelemetryOutcome.FAILED, TelemetryOutcome.TIMED_OUT}:
            self._telemetry.metric("platform.executor.failures", 1.0, context=context)
        self._telemetry.log(
            severity=(
                TelemetrySeverity.ERROR
                if outcome in {TelemetryOutcome.FAILED, TelemetryOutcome.TIMED_OUT}
                else TelemetrySeverity.INFO
            ),
            component=FailureComponent.EXECUTION,
            event_name="executor.completed",
            context=context,
            outcome=outcome,
            failure=failure,
            duration_seconds=duration,
            attributes={
                "executor_id": self.descriptor.executor_id,
                "result_code": result.result_code,
            },
        )
        self._telemetry.timeline(
            event_name="executor.completed",
            component=FailureComponent.EXECUTION,
            context=context,
            outcome=outcome,
            failure=failure,
            duration_seconds=duration,
        )
        return result

    @staticmethod
    def _failure_from_exception(
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


class ObservedModelProvider(ModelProvider):
    """Optional child instrumentation contract for model providers added in later stages."""

    def __init__(self, provider: ModelProvider, telemetry: Telemetry) -> None:
        self._provider = provider
        self._telemetry = telemetry

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._provider.descriptor

    async def generate(self, request: ModelRequest) -> ModelResponse:
        context = TelemetryContext(
            project_id=request.context.project_id,
            task_id=(
                request.context.correlation_id
                if request.context.correlation_id.startswith("task_")
                else None
            ),
            model_call_id=request.request_id,
            model_provider_id=self.descriptor.provider_id,
            correlation_id=request.context.correlation_id,
            causation_id=request.context.causation_id,
            provider_id=self.descriptor.provider_id,
        )
        parent = self._telemetry.get_anchor("task", request.context.correlation_id)
        span = self._telemetry.start_span("model.generate", context=context, parent=parent)
        self._telemetry.metric("platform.model.calls", 1.0, context=context)
        try:
            response = await self._provider.generate(request)
        except Exception as exc:
            failure = ObservedExecutor._failure_from_exception(
                exc, FailureComponent.MODEL_PROVIDER_ROUTER
            )
            record = self._telemetry.finish_span(
                span,
                outcome=TelemetryOutcome.FAILED,
                failure=failure,
            )
            self._telemetry.metric("platform.model.failures", 1.0, context=context)
            self._telemetry.log(
                severity=TelemetrySeverity.ERROR,
                component=FailureComponent.MODEL_PROVIDER_ROUTER,
                event_name="model.failed",
                context=context,
                outcome=TelemetryOutcome.FAILED,
                failure=failure,
                duration_seconds=record.duration_seconds,
            )
            raise
        record = self._telemetry.finish_span(span, outcome=TelemetryOutcome.SUCCEEDED)
        self._telemetry.metric(
            "platform.model.duration_seconds",
            record.duration_seconds,
            context=context,
            unit="seconds",
            attributes={"model_ref": response.model_ref},
        )
        self._telemetry.log(
            severity=TelemetrySeverity.INFO,
            component=FailureComponent.MODEL_PROVIDER_ROUTER,
            event_name="model.completed",
            context=context,
            outcome=TelemetryOutcome.SUCCEEDED,
            duration_seconds=record.duration_seconds,
            attributes={"model_ref": response.model_ref},
        )
        return response


class ObservedToolProvider(ToolProvider):
    """Optional child instrumentation contract that never captures tool I/O by default."""

    def __init__(self, provider: ToolProvider, telemetry: Telemetry) -> None:
        self._provider = provider
        self._telemetry = telemetry

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._provider.descriptor

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        context = TelemetryContext(
            project_id=invocation.context.project_id,
            task_id=(
                invocation.context.correlation_id
                if invocation.context.correlation_id.startswith("task_")
                else None
            ),
            tool_invocation_id=invocation.invocation_id,
            correlation_id=invocation.context.correlation_id,
            causation_id=invocation.context.causation_id,
            provider_id=self.descriptor.provider_id,
        )
        parent = self._telemetry.get_anchor("task", invocation.context.correlation_id)
        span = self._telemetry.start_span("tool.invoke", context=context, parent=parent)
        self._telemetry.metric("platform.tool.calls", 1.0, context=context)
        try:
            result = await self._provider.invoke(invocation)
        except Exception as exc:
            failure = ObservedExecutor._failure_from_exception(
                exc, FailureComponent.CAPABILITY_TOOL
            )
            record = self._telemetry.finish_span(
                span,
                outcome=TelemetryOutcome.FAILED,
                failure=failure,
            )
            self._telemetry.metric("platform.tool.failures", 1.0, context=context)
            self._telemetry.log(
                severity=TelemetrySeverity.ERROR,
                component=FailureComponent.CAPABILITY_TOOL,
                event_name="tool.failed",
                context=context,
                outcome=TelemetryOutcome.FAILED,
                failure=failure,
                duration_seconds=record.duration_seconds,
                attributes={"tool_ref": invocation.tool_ref},
            )
            raise
        record = self._telemetry.finish_span(span, outcome=TelemetryOutcome.SUCCEEDED)
        self._telemetry.metric(
            "platform.tool.duration_seconds",
            record.duration_seconds,
            context=context,
            unit="seconds",
            attributes={"tool_ref": invocation.tool_ref},
        )
        self._telemetry.log(
            severity=TelemetrySeverity.INFO,
            component=FailureComponent.CAPABILITY_TOOL,
            event_name="tool.completed",
            context=context,
            outcome=TelemetryOutcome.SUCCEEDED,
            duration_seconds=record.duration_seconds,
            attributes={"tool_ref": invocation.tool_ref},
        )
        return result
