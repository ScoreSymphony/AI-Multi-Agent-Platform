"""Streaming-transparent observability wrapper for canonical model providers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    JsonValue,
    ModelRequest,
    ModelStreamEvent,
    ModelStreamEventKind,
)

from .exporters import SpanHandle
from .models import (
    FailureClassification,
    FailureComponent,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
)
from .progressive import ObservedModelProvider as _GenerateObservedModelProvider


class ObservedModelProvider(_GenerateObservedModelProvider):
    """Observe model providers without degrading native provider capabilities.

    The earlier observability decorator only overrode ``generate``. Because the
    canonical ``ModelProvider.stream`` method has a generate-based compatibility
    fallback, wrapping a native streaming provider therefore collapsed its
    incremental stream into one completed response. This wrapper delegates the
    native stream directly while instrumenting the call and also forwards the
    optional native-model discovery capability.
    """

    async def list_native_models(self) -> tuple[str, ...]:
        """Preserve optional provider-native inventory discovery through the wrapper."""

        return await self._provider.list_native_models()

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        """Forward native stream events incrementally while recording safe telemetry."""

        async def iterate() -> AsyncIterator[ModelStreamEvent]:
            context = self._stream_context(request)
            parent = self.hierarchy.parent_for(context)
            span = self._telemetry.start_span("model.stream", context=context, parent=parent)
            self._telemetry.metric("platform.model.calls", 1.0, context=context)
            self._telemetry.log(
                severity=TelemetrySeverity.INFO,
                component=FailureComponent.MODEL_PROVIDER_ROUTER,
                event_name="model.stream.started",
                context=context,
                attributes={"provider_id": self.descriptor.provider_id},
            )

            completed: ModelStreamEvent | None = None
            try:
                async for event in self._provider.stream(request):
                    if event.kind is ModelStreamEventKind.COMPLETED:
                        completed = event
                    yield event
            except asyncio.CancelledError:
                failure = FailureClassification(
                    component=FailureComponent.MODEL_PROVIDER_ROUTER,
                    code=ErrorCode.CANCELLED.value,
                    retryable=True,
                )
                self._finish_stream_failure(
                    span,
                    context,
                    TelemetryOutcome.CANCELLED,
                    failure,
                )
                raise
            except TimeoutError:
                failure = FailureClassification(
                    component=FailureComponent.MODEL_PROVIDER_ROUTER,
                    code=ErrorCode.TIMEOUT.value,
                    retryable=True,
                )
                self._finish_stream_failure(
                    span,
                    context,
                    TelemetryOutcome.TIMED_OUT,
                    failure,
                )
                raise
            except ContractError as exc:
                outcome = TelemetryOutcome.FAILED
                if exc.code is ErrorCode.CANCELLED:
                    outcome = TelemetryOutcome.CANCELLED
                elif exc.code is ErrorCode.TIMEOUT:
                    outcome = TelemetryOutcome.TIMED_OUT
                failure = FailureClassification(
                    component=FailureComponent.MODEL_PROVIDER_ROUTER,
                    code=exc.code.value,
                    retryable=exc.retryable,
                )
                self._finish_stream_failure(span, context, outcome, failure)
                raise
            except Exception as exc:
                failure = self.hierarchy.failure_from_exception(
                    exc,
                    FailureComponent.MODEL_PROVIDER_ROUTER,
                )
                self._finish_stream_failure(
                    span,
                    context,
                    TelemetryOutcome.FAILED,
                    failure,
                )
                raise
            else:
                record = self._telemetry.finish_span(
                    span,
                    outcome=TelemetryOutcome.SUCCEEDED,
                )
                self._telemetry.metric(
                    "platform.model.duration_seconds",
                    record.duration_seconds,
                    context=context,
                    unit="seconds",
                    attributes={"outcome": TelemetryOutcome.SUCCEEDED.value},
                )
                if completed is not None:
                    self._emit_usage(completed, context)
                model_ref = completed.model_ref if completed is not None else None
                attributes: dict[str, JsonValue] = {
                    "provider_id": self.descriptor.provider_id,
                }
                if model_ref is not None:
                    attributes["model_ref"] = model_ref
                self._telemetry.log(
                    severity=TelemetrySeverity.INFO,
                    component=FailureComponent.MODEL_PROVIDER_ROUTER,
                    event_name="model.stream.completed",
                    context=context,
                    outcome=TelemetryOutcome.SUCCEEDED,
                    duration_seconds=record.duration_seconds,
                    attributes=attributes,
                )
                self._telemetry.timeline(
                    event_name="model.stream.completed",
                    component=FailureComponent.MODEL_PROVIDER_ROUTER,
                    context=context,
                    outcome=TelemetryOutcome.SUCCEEDED,
                    duration_seconds=record.duration_seconds,
                    attributes=attributes,
                )

        return iterate()

    def _stream_context(self, request: ModelRequest) -> TelemetryContext:
        task_id = self._request_metadata_id(request, "task_id")
        if task_id is None and request.context.correlation_id.startswith("task_"):
            task_id = request.context.correlation_id
        return TelemetryContext(
            project_id=request.context.project_id,
            task_id=task_id,
            run_id=self._request_metadata_id(request, "run_id"),
            agent_id=self._request_metadata_id(request, "agent_id"),
            model_call_id=request.request_id,
            model_config_id=self._request_metadata_id(request, "model_config_id"),
            model_provider_id=self.descriptor.provider_id,
            correlation_id=request.context.correlation_id,
            causation_id=request.context.causation_id,
            provider_id=self.descriptor.provider_id,
        )

    @staticmethod
    def _request_metadata_id(request: ModelRequest, key: str) -> str | None:
        value = request.requirements.get(key)
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    def _emit_usage(
        self,
        completed: ModelStreamEvent,
        context: TelemetryContext,
    ) -> None:
        usage = completed.usage
        if not usage and completed.response is not None:
            usage = completed.response.usage
        for usage_key, value in usage.items():
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            unit = "tokens" if "token" in usage_key.casefold() else "provider_units"
            self._telemetry.metric(
                "platform.model.usage",
                float(value),
                context=context,
                unit=unit,
                attributes={"usage_key": usage_key, "model_ref": completed.model_ref},
            )

    def _finish_stream_failure(
        self,
        span: SpanHandle,
        context: TelemetryContext,
        outcome: TelemetryOutcome,
        failure: FailureClassification,
    ) -> None:
        record = self._telemetry.finish_span(span, outcome=outcome, failure=failure)
        self._telemetry.metric("platform.model.failures", 1.0, context=context)
        self._telemetry.metric(
            "platform.model.duration_seconds",
            record.duration_seconds,
            context=context,
            unit="seconds",
            attributes={"outcome": outcome.value},
        )
        self._telemetry.log(
            severity=TelemetrySeverity.ERROR,
            component=FailureComponent.MODEL_PROVIDER_ROUTER,
            event_name="model.stream.failed",
            context=context,
            outcome=outcome,
            failure=failure,
            duration_seconds=record.duration_seconds,
            attributes={"provider_id": self.descriptor.provider_id},
        )
        self._telemetry.timeline(
            event_name="model.stream.failed",
            component=FailureComponent.MODEL_PROVIDER_ROUTER,
            context=context,
            outcome=outcome,
            failure=failure,
            duration_seconds=record.duration_seconds,
            attributes={"provider_id": self.descriptor.provider_id},
        )
