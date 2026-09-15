from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel.models import TaskState
from ai_multi_agent_platform.kernel.repository import CommandRecord
from ai_multi_agent_platform.kernel.run_commands import KernelRunCommands, RunCommandKernelHost


class _AttachmentHost:
    def __init__(self) -> None:
        self.invalidation_started = asyncio.Event()
        self.release_invalidation = asyncio.Event()
        self.committed = asyncio.Event()
        self.task_state = cast(TaskState, object())

    async def get_task(self, task_id: str) -> TaskState:
        return self.task_state

    async def _task_command(
        self,
        task_id: str,
        key: str,
        operation: str,
    ) -> CommandRecord | None:
        return None

    async def _invalidate_completion_subject(self, task_id: str) -> None:
        self.invalidation_started.set()
        await self.release_invalidation.wait()

    async def _commit_task_command(self, **kwargs: Any) -> CommandRecord:
        self.committed.set()
        return cast(CommandRecord, object())


@pytest.mark.parametrize("kind", ["artifact", "result"])
def test_output_attachment_settles_after_cancellation_during_invalidation(kind: str) -> None:
    async def scenario() -> None:
        host = _AttachmentHost()
        commands = KernelRunCommands(cast(RunCommandKernelHost, host))
        task_id = new_id("task")
        if kind == "artifact":
            operation = asyncio.create_task(
                commands.attach_artifact(
                    idempotency_key="attach-cancel-artifact",
                    task_id=task_id,
                    artifact_id=new_id("artifact"),
                )
            )
        else:
            operation = asyncio.create_task(
                commands.attach_result(
                    idempotency_key="attach-cancel-result",
                    task_id=task_id,
                    result_id=new_id("result"),
                )
            )

        await host.invalidation_started.wait()
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
        assert not host.committed.is_set()

        host.release_invalidation.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert host.committed.is_set()

    asyncio.run(scenario())
