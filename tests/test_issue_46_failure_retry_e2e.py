from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ModelRequest,
    OperationContext,
    ToolInvocation,
)
from ai_multi_agent_platform.domain import RunStatus, TaskStatus, new_id
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.observability import (
    FailureComponent,
    InMemoryExporter,
    ObservabilityEventProvider,
    ObservedExecutor,
    ObservedModelProvider,
    ObservedToolProvider,
    Telemetry,
    TelemetryOutcome,
)
from ai_multi_agent_platform.testing import (
    FakeFailure,
    FakeModelProvider,
    FakeOrchestrator,
    FakeToolProvider,
)


def test_controlled_failure_retry_preserves_canonical_history_and_retry_telemetry(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        repository = InMemoryKernelRepository()
        exporter = InMemoryExporter()
        telemetry = Telemetry(exporter)
        event_sink = ObservabilityEventProvider(telemetry)
        workspace_root = tmp_path / "workspaces"
        workspace = workspace_root / task_id
        workspace.mkdir(parents=True)

        failing_kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=ExecutorLifecycleBackend(
                ObservedExecutor(ReferenceExecutor(workspace_root), telemetry),
                workspace=task_id,
                action="fail",
            ),
            repository=repository,
            event_sink=event_sink,
        )

        await failing_kernel.create_task(
            idempotency_key="issue-46-failure-create",
            task_id=task_id,
            title="Controlled failure and retry",
            objective="Prove component failures preserve canonical retry and telemetry semantics.",
            owner_type="user",
            owner_id="issue-46",
            actor_ref="user:issue-46",
        )
        await failing_kernel.ready_task(
            idempotency_key="issue-46-failure-ready",
            task_id=task_id,
            actor_ref="user:issue-46",
        )
        first = await failing_kernel.start_task(
            idempotency_key="issue-46-failure-start",
            task_id=task_id,
            actor_ref="user:issue-46",
        )
        assert first.status is RunStatus.RUNNING
        assert first.attempt == 1

        failed = await failing_kernel.refresh_run(
            idempotency_key="issue-46-failure-refresh",
            task_id=task_id,
            run_id=first.run_id,
            actor_ref="user:issue-46",
        )
        assert failed.status is RunStatus.FAILED
        assert "controlled failure" in str(failed.output["stderr"])
        assert (await failing_kernel.get_task(task_id)).status is TaskStatus.FAILED
        assert tuple(workspace.iterdir()) == ()

        failed_executor_log = next(
            log
            for log in exporter.logs
            if log.event_name == "executor.completed" and log.context.run_id == first.run_id
        )
        assert failed_executor_log.outcome is TelemetryOutcome.FAILED
        assert failed_executor_log.failure is not None
        assert failed_executor_log.failure.component is FailureComponent.EXECUTION
        assert failed_executor_log.failure.code == "execution_failed"
        assert failed_executor_log.failure.retryable is False

        recovering_kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=ExecutorLifecycleBackend(
                ObservedExecutor(ReferenceExecutor(workspace_root), telemetry),
                workspace=task_id,
                action="echo",
            ),
            repository=repository,
            event_sink=event_sink,
        )
        retry = await recovering_kernel.retry_task(
            idempotency_key="issue-46-failure-retry",
            task_id=task_id,
            actor_ref="user:issue-46",
        )
        assert retry.run_id != first.run_id
        assert retry.attempt == 2
        assert retry.status is RunStatus.QUEUED
        assert (await recovering_kernel.get_task(task_id)).status is TaskStatus.READY

        started_retry = await recovering_kernel.start_run(
            idempotency_key="issue-46-failure-retry-start",
            task_id=task_id,
            run_id=retry.run_id,
            actor_ref="user:issue-46",
        )
        assert started_retry.status is RunStatus.RUNNING
        assert started_retry.attempt == 2

        recovered = await recovering_kernel.refresh_run(
            idempotency_key="issue-46-failure-retry-refresh",
            task_id=task_id,
            run_id=retry.run_id,
            actor_ref="user:issue-46",
        )
        assert recovered.status is RunStatus.SUCCEEDED
        assert (await recovering_kernel.get_task(task_id)).status is TaskStatus.SUCCEEDED

        recovered_executor_log = next(
            log
            for log in exporter.logs
            if log.event_name == "executor.completed" and log.context.run_id == retry.run_id
        )
        assert recovered_executor_log.outcome is TelemetryOutcome.SUCCEEDED

        operation = OperationContext(
            correlation_id=task_id,
            owner_type="user",
            owner_id="issue-46",
        )
        failing_model = ObservedModelProvider(
            FakeModelProvider(
                failure=FakeFailure(
                    ErrorCode.UNAVAILABLE,
                    "model temporarily unavailable",
                    retryable=True,
                )
            ),
            telemetry,
        )
        model_request = ModelRequest(
            request_id="issue-46-model-failure",
            messages=("deterministic failure probe",),
            context=operation,
        )
        with pytest.raises(ContractError) as model_error:
            await failing_model.generate(model_request)
        assert model_error.value.code is ErrorCode.UNAVAILABLE
        assert model_error.value.retryable is True

        model_recovery = await ObservedModelProvider(
            FakeModelProvider(response_text="model recovered"),
            telemetry,
        ).generate(
            ModelRequest(
                request_id="issue-46-model-recovery",
                messages=("deterministic recovery probe",),
                context=operation,
            )
        )
        assert model_recovery.text == "model recovered"

        tool_ref = new_id("tool")
        failing_tool = ObservedToolProvider(
            FakeToolProvider(
                failure=FakeFailure(
                    ErrorCode.TIMEOUT,
                    "tool timed out",
                    retryable=True,
                )
            ),
            telemetry,
        )
        tool_invocation = ToolInvocation(
            invocation_id="issue-46-tool-failure",
            tool_ref=tool_ref,
            arguments={"probe": True},
            context=operation,
        )
        with pytest.raises(ContractError) as tool_error:
            await failing_tool.invoke(tool_invocation)
        assert tool_error.value.code is ErrorCode.TIMEOUT
        assert tool_error.value.retryable is True

        tool_recovery = await ObservedToolProvider(
            FakeToolProvider(fixed_output={"recovered": True}, echo_arguments=False),
            telemetry,
        ).invoke(
            ToolInvocation(
                invocation_id="issue-46-tool-recovery",
                tool_ref=tool_ref,
                arguments={"probe": True},
                context=operation,
            )
        )
        assert tool_recovery.output == {"recovered": True}

        history = await recovering_kernel.history(task_id)
        event_types = [event.event_type for event in history]
        assert event_types.count("run.created") == 2
        assert event_types.count("run.failed") == 1
        assert event_types.count("run.succeeded") == 1
        assert event_types.count("task.failed") == 1
        assert event_types.count("task.succeeded") == 1

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

        model_failure_log = next(log for log in exporter.logs if log.event_name == "model.failed")
        assert model_failure_log.failure is not None
        assert model_failure_log.failure.component is FailureComponent.MODEL_PROVIDER_ROUTER
        assert model_failure_log.failure.code == ErrorCode.UNAVAILABLE.value
        assert model_failure_log.failure.retryable is True

        tool_failure_log = next(log for log in exporter.logs if log.event_name == "tool.failed")
        assert tool_failure_log.failure is not None
        assert tool_failure_log.failure.component is FailureComponent.CAPABILITY_TOOL
        assert tool_failure_log.failure.code == ErrorCode.TIMEOUT.value
        assert tool_failure_log.failure.retryable is True

        metric_names = {metric.name for metric in exporter.metrics}
        assert {
            "platform.executor.failures",
            "platform.model.failures",
            "platform.tool.failures",
            "platform.run.retries",
        } <= metric_names

    asyncio.run(scenario())
