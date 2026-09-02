from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.contracts import ModelRequest, OperationContext, ToolInvocation
from ai_multi_agent_platform.domain import Event, new_id
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.observability import (
    CaptureKind,
    CapturePolicy,
    DependencyHealth,
    FailureComponent,
    InMemoryExporter,
    ObservabilityEventProvider,
    ObservedExecutor,
    ObservedModelProvider,
    ObservedToolProvider,
    ReadinessState,
    Telemetry,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
    TraceCarrier,
    aggregate_health,
)
from ai_multi_agent_platform.testing import FakeModelProvider, FakeOrchestrator, FakeToolProvider


def test_task_run_executor_flow_shares_trace_and_canonical_identifiers(tmp_path: Path) -> None:
    task_id = new_id("task")
    workspace_root = tmp_path / "workspaces"
    (workspace_root / task_id).mkdir(parents=True)
    exporter = InMemoryExporter()
    telemetry = Telemetry(exporter)
    event_sink = ObservabilityEventProvider(telemetry)
    executor = ObservedExecutor(ReferenceExecutor(workspace_root), telemetry)
    lifecycle = ExecutorLifecycleBackend(
        executor,
        workspace=task_id,
        action="write_artifact",
    )
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=lifecycle,
        event_sink=event_sink,
    )

    async def scenario() -> str:
        await kernel.create_task(
            idempotency_key="obs-create",
            task_id=task_id,
            title="Observable task",
            objective="Trace this flow",
            owner_type="user",
            owner_id="tester",
        )
        await kernel.ready_task(idempotency_key="obs-ready", task_id=task_id)
        run = await kernel.start_task(idempotency_key="obs-start", task_id=task_id)
        await kernel.refresh_run(
            idempotency_key="obs-refresh",
            task_id=task_id,
            run_id=run.run_id,
        )
        return run.run_id

    run_id = asyncio.run(scenario())

    task_span = next(span for span in exporter.spans if span.name == "task.lifecycle")
    run_span = next(span for span in exporter.spans if span.name == "run.lifecycle")
    executor_span = next(span for span in exporter.spans if span.name == "executor.execute")
    assert task_span.trace_id == run_span.trace_id == executor_span.trace_id
    assert run_span.parent_span_id == task_span.span_id
    assert executor_span.parent_span_id == run_span.span_id
    assert executor_span.context.task_id == task_id
    assert executor_span.context.run_id == run_id
    assert executor_span.context.correlation_id == task_id

    executor_log = next(log for log in exporter.logs if log.event_name == "executor.completed")
    assert executor_log.context.task_id == task_id
    assert executor_log.context.run_id == run_id
    assert executor_log.context.correlation_id == task_id
    assert executor_log.outcome is TelemetryOutcome.SUCCEEDED

    timeline = exporter.query_timeline(task_id=task_id)
    names = {entry.event_name for entry in timeline}
    assert {"task.created", "run.created", "executor.completed", "run.succeeded"} <= names
    assert {metric.name for metric in exporter.metrics} >= {
        "platform.lifecycle.events",
        "platform.executor.calls",
        "platform.executor.duration_seconds",
        "platform.run.queue_wait_seconds",
        "platform.run.duration_seconds",
    }


def test_executor_failure_is_classified_without_leaking_backend_exception(tmp_path: Path) -> None:
    task_id = new_id("task")
    workspace_root = tmp_path / "workspaces"
    (workspace_root / task_id).mkdir(parents=True)
    exporter = InMemoryExporter()
    telemetry = Telemetry(exporter)
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=ExecutorLifecycleBackend(
            ObservedExecutor(ReferenceExecutor(workspace_root), telemetry),
            workspace=task_id,
            action="fail",
        ),
        event_sink=ObservabilityEventProvider(telemetry),
    )

    async def scenario() -> None:
        await kernel.create_task(
            idempotency_key="fail-create",
            task_id=task_id,
            title="Fail observably",
            objective="Controlled failure",
            owner_type="user",
            owner_id="tester",
        )
        await kernel.ready_task(idempotency_key="fail-ready", task_id=task_id)
        run = await kernel.start_task(idempotency_key="fail-start", task_id=task_id)
        await kernel.refresh_run(
            idempotency_key="fail-refresh",
            task_id=task_id,
            run_id=run.run_id,
        )

    asyncio.run(scenario())
    log = next(log for log in exporter.logs if log.event_name == "executor.completed")
    assert log.outcome is TelemetryOutcome.FAILED
    assert log.failure is not None
    assert log.failure.component is FailureComponent.EXECUTION
    assert log.failure.code == "execution_failed"
    assert any(metric.name == "platform.executor.failures" for metric in exporter.metrics)


def test_capture_policy_is_default_deny_and_recursively_redacts_secrets() -> None:
    policy = CapturePolicy()
    safe = policy.redact(
        {
            "password": "p",
            "token_count": 42,
            "nested": {"api_key": "key", "visible": "yes"},
        }
    )
    assert safe["password"] == "[REDACTED]"
    assert safe["token_count"] == 42
    assert safe["nested"] == {"api_key": "[REDACTED]", "visible": "yes"}
    assert policy.redact({"text": "private prompt"}, kind=CaptureKind.PROMPT) == {
        "capture": "[REDACTED]"
    }
    assert policy.redact({"output": "private tool output"}, kind=CaptureKind.TOOL_OUTPUT) == {
        "capture": "[REDACTED]"
    }


def test_noop_exporter_mode_requires_no_external_backend() -> None:
    telemetry = Telemetry()
    context = TelemetryContext(correlation_id="local-only")
    telemetry.log(
        severity=TelemetrySeverity.INFO,
        component=FailureComponent.DOMAIN_KERNEL,
        event_name="local.noop",
        context=context,
    )
    telemetry.metric("local.noop.count", 1.0, context=context)
    span = telemetry.start_span("local.noop.span", context=context)
    finished = telemetry.finish_span(span, outcome=TelemetryOutcome.SUCCEEDED)
    assert finished.outcome is TelemetryOutcome.SUCCEEDED


def test_health_aggregation_distinguishes_optional_and_required_dependencies() -> None:
    optional_down = aggregate_health(
        (
            DependencyHealth(
                name="optional-adapter",
                state=ReadinessState.UNAVAILABLE,
                required=False,
            ),
        )
    )
    assert optional_down.alive
    assert optional_down.ready
    assert optional_down.readiness is ReadinessState.DEGRADED

    required_down = aggregate_health(
        (
            DependencyHealth(
                name="required-store",
                state=ReadinessState.UNAVAILABLE,
                required=True,
            ),
        )
    )
    assert required_down.alive
    assert not required_down.ready
    assert required_down.readiness is ReadinessState.UNAVAILABLE
    assert aggregate_health(draining=True).readiness is ReadinessState.DRAINING
    assert not aggregate_health(alive=False).alive


def test_model_and_tool_wrappers_create_children_without_content_capture() -> None:
    task_id = new_id("task")
    exporter = InMemoryExporter()
    telemetry = Telemetry(exporter)
    root = telemetry.start_span(
        "task.lifecycle",
        context=TelemetryContext(task_id=task_id, correlation_id=task_id),
    )
    telemetry.set_anchor("task", task_id, root)
    model = ObservedModelProvider(
        FakeModelProvider(response_text="PRIVATE_RESPONSE"),
        telemetry,
    )
    tool = ObservedToolProvider(FakeToolProvider(), telemetry)
    context = OperationContext(correlation_id=task_id, causation_id="parent-operation")

    async def scenario() -> None:
        await model.generate(
            ModelRequest(
                request_id="model-call-1",
                messages=("PRIVATE_PROMPT",),
                context=context,
            )
        )
        await tool.invoke(
            ToolInvocation(
                invocation_id="tool-call-1",
                tool_ref="example.tool",
                arguments={"secret": "PRIVATE_TOOL_INPUT"},
                context=context,
            )
        )

    asyncio.run(scenario())
    model_span = next(span for span in exporter.spans if span.name == "model.generate")
    tool_span = next(span for span in exporter.spans if span.name == "tool.invoke")
    assert model_span.parent_span_id == root.span_id
    assert tool_span.parent_span_id == root.span_id
    serialized_logs = repr(exporter.logs)
    assert "PRIVATE_PROMPT" not in serialized_logs
    assert "PRIVATE_RESPONSE" not in serialized_logs
    assert "PRIVATE_TOOL_INPUT" not in serialized_logs


def test_trace_carrier_round_trips_remote_context_without_transport_dependency() -> None:
    task_id = new_id("task")
    run_id = new_id("run")
    telemetry = Telemetry(InMemoryExporter())
    span = telemetry.start_span(
        "worker.dispatch",
        context=TelemetryContext(
            task_id=task_id,
            run_id=run_id,
            correlation_id=task_id,
            causation_id="dispatch-command",
        ),
    )
    carrier = TraceCarrier.from_span(span)
    recovered = TraceCarrier.from_mapping(carrier.to_mapping())
    assert recovered == carrier
    assert recovered.child_context().task_id == task_id
    assert recovered.child_context().run_id == run_id


def test_event_provider_preserves_correlation_across_async_read_and_subscription() -> None:
    task_id = new_id("task")
    exporter = InMemoryExporter()
    provider = ObservabilityEventProvider(Telemetry(exporter))
    event = Event(
        event_type="task.created",
        subject_type="task",
        subject_id=task_id,
        correlation_id=task_id,
    )

    async def scenario() -> tuple[int, int]:
        await provider.publish(event)
        await provider.publish(event)
        read = await provider.read(task_id)
        subscribed = [item async for item in provider.subscribe(task_id)]
        return len(read), len(subscribed)

    assert asyncio.run(scenario()) == (1, 1)
    timeline = exporter.query_timeline(correlation_id=task_id)
    assert len(timeline) == 1
    assert timeline[0].context.correlation_id == task_id
