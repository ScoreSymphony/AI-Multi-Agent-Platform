from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ai_multi_agent_platform.capabilities import InvocationRecord, InvocationStatus, InvocationTrace
from ai_multi_agent_platform.contracts import ModelRequest, ModelResponse, OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.observability import (
    AccountingBridgeExporter,
    FailureComponent,
    InMemoryExporter,
    ObservabilityInvocationObserver,
    ObservedModelProvider,
    Telemetry,
    TelemetryContext,
)
from ai_multi_agent_platform.testing import FakeModelProvider


class _UsageModelProvider(FakeModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        response = await super().generate(request)
        return ModelResponse(
            request_id=response.request_id,
            text=response.text,
            model_ref=response.model_ref,
            usage={
                "input_tokens": 4,
                "output_tokens": 2,
                "provider_note": "not-a-number",
            },
        )


@dataclass
class _RecordingMeasurementSink:
    records: list[object]

    def ingest_metric(self, record: object) -> None:
        self.records.append(record)


def test_model_usage_metrics_emit_only_reported_numeric_measurements() -> None:
    task_id = new_id("task")
    exporter = InMemoryExporter()
    telemetry = Telemetry(exporter)
    model = ObservedModelProvider(_UsageModelProvider(), telemetry)

    async def scenario() -> None:
        await model.generate(
            ModelRequest(
                request_id="usage-call",
                messages=("private",),
                context=OperationContext(correlation_id=task_id),
            )
        )

    asyncio.run(scenario())
    usage = [metric for metric in exporter.metrics if metric.name == "platform.model.usage"]
    assert len(usage) == 2
    assert {metric.attributes["usage_key"] for metric in usage} == {
        "input_tokens",
        "output_tokens",
    }
    assert all(metric.unit == "tokens" for metric in usage)


def test_capability_policy_and_approval_outcomes_are_observable() -> None:
    task_id = new_id("task")
    agent_id = new_id("agent")
    trace = InvocationTrace(
        correlation_id=task_id,
        task_id=task_id,
        run_id=new_id("run"),
        agent_id=agent_id,
    )
    exporter = InMemoryExporter()
    observer = ObservabilityInvocationObserver(Telemetry(exporter))

    def record(
        invocation_id: str,
        status: InvocationStatus,
        *,
        approval_decision: str | None = None,
        error_code: str | None = None,
    ) -> InvocationRecord:
        return InvocationRecord(
            invocation_id=invocation_id,
            capability_id="tool.echo",
            capability_version="1.0",
            provider_id="native",
            provider_tool_ref="echo",
            status=status,
            trace=trace,
            approval_decision=approval_decision,
            error_code=error_code,
        )

    async def scenario() -> None:
        await observer.record(record("denied", InvocationStatus.DENIED, error_code="forbidden"))
        await observer.record(record("approval", InvocationStatus.APPROVAL_REQUIRED))
        await observer.record(
            record("approved", InvocationStatus.RUNNING, approval_decision="approved")
        )
        await observer.record(
            record("approved", InvocationStatus.SUCCEEDED, approval_decision="approved")
        )

    asyncio.run(scenario())
    metric_names = {metric.name for metric in exporter.metrics}
    assert "platform.tool.denied" in metric_names
    assert "platform.tool.approval_required" in metric_names
    assert "platform.tool.approved" in metric_names
    names = {entry.event_name for entry in exporter.timeline}
    assert "capability.invocation.denied" in names
    assert "capability.invocation.approval_required" in names
    denied = next(entry for entry in exporter.timeline if entry.event_name.endswith("denied"))
    assert denied.failure is not None
    assert denied.failure.component is FailureComponent.CAPABILITY_TOOL
    assert denied.context.agent_id == agent_id


def test_accounting_bridge_forwards_measurements_without_owning_accounting_state() -> None:
    delegate = InMemoryExporter()
    sink = _RecordingMeasurementSink(records=[])
    bridge = AccountingBridgeExporter(delegate, sink)
    Telemetry(bridge).metric(
        "platform.executor.duration_seconds",
        1.25,
        context=TelemetryContext(task_id=new_id("task"), correlation_id="usage-correlation"),
        unit="seconds",
    )
    assert len(delegate.metrics) == 1
    assert sink.records == [delegate.metrics[0]]
    assert not hasattr(bridge, "budgets")
    assert not hasattr(bridge, "costs")
