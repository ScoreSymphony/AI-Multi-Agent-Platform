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
        action="write_artifact",
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
        assert run.output["result_code"] == 0
        assert run.output["artifacts"] == ("artifact.txt",)
        assert (workspace / "artifact.txt").exists()

        artifact_id = new_id("artifact")
        await kernel.attach_artifact(
            idempotency_key="cmd-attach-artifact",
            task_id=task_id,
            run_id=run.run_id,
            artifact_id=artifact_id,
        )
        run = await kernel.get_run(task_id, run.run_id)
        task = await kernel.get_task(task_id)
        assert artifact_id in run.artifact_ids
        assert artifact_id in task.artifact_ids
        assert task.status is TaskStatus.SUCCEEDED

    asyncio.run(scenario())
