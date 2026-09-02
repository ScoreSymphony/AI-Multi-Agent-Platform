from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.execution import (
    ExecutorLifecycleBackend,
    ReferenceExecutor,
)
from ai_multi_agent_platform.kernel import PlatformKernel, TaskStatus
from ai_multi_agent_platform.testing import FakeEventProvider, FakeOrchestrator


def test_kernel_executes_end_to_end_through_reference_executor(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace = workspace_root / "task-demo"
    workspace.mkdir(parents=True)
    lifecycle = ExecutorLifecycleBackend(
        ReferenceExecutor(workspace_root),
        workspace="task-demo",
        action="echo",
    )
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=lifecycle,
        events=FakeEventProvider(),
    )

    async def scenario() -> None:
        await kernel.create_task(
            command_id="cmd-create",
            task_id="task-demo",
            title="Demo",
            objective="Prove platform-owned execution",
            owner_type="user",
            owner_id="tester",
        )
        await kernel.ready_task(command_id="cmd-ready", task_id="task-demo")
        run = await kernel.start_task(
            command_id="cmd-start",
            task_id="task-demo",
        )
        assert run.status.value == "succeeded"
        task = await kernel.get_task("task-demo")
        assert task.status is TaskStatus.SUCCEEDED
        assert run.output["result_code"] == 0

    asyncio.run(scenario())
