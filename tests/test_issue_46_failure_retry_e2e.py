from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import ExecutionStatus
from ai_multi_agent_platform.domain import RunStatus, TaskStatus
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.observability import (
    InMemoryExporter,
    ObservabilityEventProvider,
    Telemetry,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def test_controlled_failure_retry_preserves_canonical_history_and_retry_telemetry() -> None:
    async def scenario() -> None:
        lifecycle = FakeLifecycleBackend()
        repository = InMemoryKernelRepository()
        exporter = InMemoryExporter()
        event_sink = ObservabilityEventProvider(Telemetry(exporter))
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=repository,
            event_sink=event_sink,
        )

        task = await kernel.create_task(
            idempotency_key="issue-46-failure-create",
            title="Controlled failure and retry",
            objective="Prove canonical retry state and telemetry remain coherent.",
            owner_type="user",
            owner_id="issue-46",
            actor_ref="user:issue-46",
        )
        task_id = task.task_id
        await kernel.ready_task(
            idempotency_key="issue-46-failure-ready",
            task_id=task_id,
            actor_ref="user:issue-46",
        )
        first = await kernel.start_task(
            idempotency_key="issue-46-failure-start",
            task_id=task_id,
            actor_ref="user:issue-46",
        )
        assert first.status is RunStatus.RUNNING
        assert first.attempt == 1

        lifecycle.complete(first.run_id, status=ExecutionStatus.FAILED)
        failed = await kernel.refresh_run(
            idempotency_key="issue-46-failure-refresh",
            task_id=task_id,
            run_id=first.run_id,
            actor_ref="user:issue-46",
        )
        assert failed.status is RunStatus.FAILED
        assert (await kernel.get_task(task_id)).status is TaskStatus.FAILED

        retry = await kernel.retry_task(
            idempotency_key="issue-46-failure-retry",
            task_id=task_id,
            actor_ref="user:issue-46",
        )
        assert retry.run_id != first.run_id
        assert retry.attempt == 2
        assert retry.status is RunStatus.QUEUED
        assert (await kernel.get_task(task_id)).status is TaskStatus.READY

        started_retry = await kernel.start_run(
            idempotency_key="issue-46-failure-retry-start",
            task_id=task_id,
            run_id=retry.run_id,
            actor_ref="user:issue-46",
        )
        assert started_retry.status is RunStatus.RUNNING
        assert started_retry.attempt == 2
        assert (await kernel.get_task(task_id)).status is TaskStatus.RUNNING

        history = await kernel.history(task_id)
        event_types = [event.event_type for event in history]
        assert event_types.count("run.created") == 2
        assert event_types.count("run.failed") == 1
        assert event_types.index("run.failed") < event_types.index("task.ready", 2)

        retry_metrics = [
            metric for metric in exporter.metrics if metric.name == "platform.run.retries"
        ]
        assert len(retry_metrics) == 1
        assert retry_metrics[0].value == 1.0
        assert retry_metrics[0].context.task_id == task_id
        assert retry_metrics[0].context.run_id == retry.run_id
        assert retry_metrics[0].attributes["attempt"] == 2

        failed_lifecycle_metrics = [
            metric
            for metric in exporter.metrics
            if metric.name == "platform.lifecycle.events"
            and metric.attributes.get("event_type") == "run.failed"
        ]
        assert len(failed_lifecycle_metrics) == 1
        assert failed_lifecycle_metrics[0].context.task_id == task_id
        assert failed_lifecycle_metrics[0].context.run_id == first.run_id

    asyncio.run(scenario())
