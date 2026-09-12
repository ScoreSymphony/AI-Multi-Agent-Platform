from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.observability import (
    FailureComponent,
    InMemoryExporter,
    ObservabilityEventProvider,
    ObservedExecutor,
    Telemetry,
    TelemetryOutcome,
)
from ai_multi_agent_platform.testing import FakeOrchestrator


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
