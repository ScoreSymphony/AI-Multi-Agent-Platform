from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

import pytest

from ai_multi_agent_platform.adapters.forge import ForgeExecutor
from ai_multi_agent_platform.adapters.forge_http import (
    ForgeHttpClient,
    ForgeHttpClientConfig,
)
from ai_multi_agent_platform.domain import RunStatus, TaskStatus, new_id
from ai_multi_agent_platform.execution import (
    CancellationToken,
    ExecutionRequest,
    ExecutorLifecycleBackend,
)
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.testing import FakeOrchestrator

BASE_URL = os.environ.get("FORGE_SIDECAR_BASE_URL")
WORKSPACE_ROOT = os.environ.get("FORGE_SIDECAR_WORKSPACE_ROOT")

pytestmark = pytest.mark.skipif(
    not BASE_URL or not WORKSPACE_ROOT,
    reason="real Forge executor sidecar is not configured",
)


def _executor(*, delay_seconds: int = 0) -> ForgeExecutor:
    assert BASE_URL is not None
    assert WORKSPACE_ROOT is not None
    client = ForgeHttpClient(
        ForgeHttpClientConfig(
            base_url=BASE_URL,
            executor_type="null",
            executor_config={"delay_seconds": delay_seconds},
            poll_interval_seconds=0.02,
            request_timeout_seconds=5.0,
        )
    )
    return ForgeExecutor(
        client,
        WORKSPACE_ROOT,
        capabilities=("execute",),
    )


def _request(
    workspace_name: str, *, cancellation: CancellationToken | None = None
) -> ExecutionRequest:
    task_id = f"task_{uuid4().hex}"
    run_id = f"run_{uuid4().hex}"
    return ExecutionRequest(
        task_id=task_id,
        run_id=run_id,
        correlation_id=task_id,
        action="execute",
        workspace=workspace_name,
        arguments={"instruction": "exercise the real Forge executor sidecar"},
        timeout_seconds=5.0,
        cancellation=cancellation,
    )


def _workspace() -> str:
    assert WORKSPACE_ROOT is not None
    name = f"integration-{uuid4().hex}"
    (Path(WORKSPACE_ROOT) / name).mkdir(parents=True)
    return name


def test_real_sidecar_health_and_execution_preserve_canonical_identity() -> None:
    async def scenario() -> None:
        executor = _executor()
        health = await executor.health()
        assert health.healthy is True
        assert health.metadata["transport"] == "http-sidecar"
        assert health.metadata["protocol_version"] == "forge-executor-sidecar/v1"

        request = _request(_workspace())
        result = await executor.execute(request)
        assert result.status is RunStatus.SUCCEEDED
        assert result.task_id == request.task_id
        assert result.run_id == request.run_id
        assert result.correlation_id == request.correlation_id
        assert result.result_code == 0
        assert result.output["summary"] == "Null executor completed successfully."
        forge_metadata = result.adapter_metadata["forge"]
        execution_id = forge_metadata["execution_id"]
        assert isinstance(execution_id, str)
        assert execution_id.startswith("forge_exec_")

    asyncio.run(scenario())


def test_real_sidecar_executes_through_canonical_kernel_lifecycle() -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        workspace = _workspace()
        lifecycle = ExecutorLifecycleBackend(
            _executor(),
            workspace=workspace,
            action="execute",
        )
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
        )

        await kernel.create_task(
            idempotency_key="issue-46-forge:create",
            task_id=task_id,
            title="Real Forge conformance",
            objective="Execute a canonical Run through the real Forge sidecar",
            owner_type="user",
            owner_id="issue-46",
        )
        await kernel.ready_task(
            idempotency_key="issue-46-forge:ready",
            task_id=task_id,
        )
        run = await kernel.start_task(
            idempotency_key="issue-46-forge:start",
            task_id=task_id,
        )
        run = await kernel.refresh_run(
            idempotency_key="issue-46-forge:refresh",
            task_id=task_id,
            run_id=run.run_id,
        )

        task = await kernel.get_task(task_id)
        history = await kernel.history(task_id)
        running = next(event for event in history if event.event_type == "run.running")
        succeeded = next(event for event in history if event.event_type == "run.succeeded")

        assert run.status is RunStatus.SUCCEEDED
        assert task.status is TaskStatus.SUCCEEDED
        assert running.payload["backend_ref"] == f"forge:{run.run_id}"
        for event in (running, succeeded):
            adapter_metadata = event.payload["adapter_metadata"]
            assert isinstance(adapter_metadata, Mapping)
            forge_metadata = adapter_metadata["forge"]
            assert isinstance(forge_metadata, Mapping)
            execution_id = forge_metadata["execution_id"]
            assert isinstance(execution_id, str)
            assert execution_id.startswith("forge_exec_")

    asyncio.run(scenario())


def test_real_sidecar_cancellation_stays_canonical() -> None:
    async def scenario() -> None:
        token = CancellationToken()
        executor = _executor(delay_seconds=2)
        request = _request(_workspace(), cancellation=token)
        task = asyncio.create_task(executor.execute(request))
        await asyncio.sleep(0.1)
        token.cancel()
        result = await task

        assert result.status is RunStatus.CANCELLED
        assert result.task_id == request.task_id
        assert result.run_id == request.run_id
        assert result.error is not None
        assert result.error.category.value == "cancelled"

    asyncio.run(scenario())
