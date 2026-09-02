from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.execution import (
    ExecutorLifecycleBackend,
    ReferenceExecutor,
)
from ai_multi_agent_platform.kernel import PlatformKernel, TaskStatus
from ai_multi_agent_platform.testing import FakeOrchestrator


def test_kernel_executes_end_to_end_through_reference_executor(
    tmp_path: Path,
) -> None:
    task_id = new_id("task")
    workspace_root = tmp_path / "workspaces"
    workspace = workspace_root / task_id
    workspace.mkdir(parents=True)
    lifecycle = ExecutorLifecycleBackend(
        ReferenceExecutor(workspace_root),
        workspace=task_id,
        action="echo",
    )
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=lifecycle,
    )

    async def scenario() -> None:
        await kernel.create_task(
            idempotency_key="cmd-create",
            task_id=task_id,
            title="Demo",
            objective="Prove platform-owned execution",
            owner_type="user",
            owner_id="tester",
        )
        await kernel.ready_task(
            idempotency_key="cmd-ready",
            task_id=task_id,
        )
        run = await kernel.start_task(
            idempotency_key="cmd-start",
            task_id=task_id,
        )
        run = await kernel.refresh_run(
            idempotency_key="cmd-refresh",
            task_id=task_id,
            run_id=run.run_id,
        )
        assert run.status.value == "succeeded"
        task = await kernel.get_task(task_id)
        assert task.status is TaskStatus.SUCCEEDED
        assert run.output["result_code"] == 0

    asyncio.run(scenario())
