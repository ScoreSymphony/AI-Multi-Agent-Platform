from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    ExecutionStatus,
)
from ai_multi_agent_platform.kernel import PlatformKernel, TaskStatus, reduce_run, reduce_task
from ai_multi_agent_platform.testing import FakeEventProvider, FakeOrchestrator
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend
from ai_multi_agent_platform.testing.sqlite_events import SqliteEventProvider


class ControllableLifecycle(FakeLifecycleBackend):
    def __init__(self) -> None:
        super().__init__()
        self.start_calls: list[str] = []

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        self.start_calls.append(request.run_id)
        return await super().start(request)

    def set_status(
        self,
        run_id: str,
        status: ExecutionStatus,
        *,
        output: dict[str, str] | None = None,
    ) -> None:
        self._runs[run_id] = ExecutionSnapshot(
            run_id=run_id,
            status=status,
            output=output or {},
        )


class CrashAfterBackendStart(ControllableLifecycle):
    def __init__(self) -> None:
        super().__init__()
        self._crash_once = True

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        handle = await super().start(request)
        if self._crash_once:
            self._crash_once = False
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "simulated process interruption after backend accepted run",
                retryable=True,
            )
        return handle


def _kernel(
    lifecycle: FakeLifecycleBackend | None = None,
    events: FakeEventProvider | SqliteEventProvider | None = None,
) -> PlatformKernel:
    return PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=lifecycle or ControllableLifecycle(),
        events=events or FakeEventProvider(),
    )


def _create_ready(kernel: PlatformKernel, task_id: str = "task-demo") -> None:
    asyncio.run(
        kernel.create_task(
            command_id="cmd-create",
            task_id=task_id,
            title="Initial title",
            objective="Complete a generic task",
            owner_type="user",
            owner_id="user-test",
            project_id="project-test",
        )
    )
    asyncio.run(
        kernel.update_task(
            command_id="cmd-update",
            task_id=task_id,
            title="Updated title",
        )
    )
    asyncio.run(kernel.ready_task(command_id="cmd-ready", task_id=task_id))


def test_complete_lifecycle_replays_to_same_task_and_run_state() -> None:
    lifecycle = ControllableLifecycle()
    events = FakeEventProvider()
    kernel = _kernel(lifecycle, events)
    _create_ready(kernel)

    run = asyncio.run(kernel.start_task(command_id="cmd-start", task_id="task-demo"))
    assert run.status is ExecutionStatus.RUNNING
    assert lifecycle.start_calls == [run.run_id]

    lifecycle.set_status(run.run_id, ExecutionStatus.SUCCEEDED, output={"answer": "done"})
    finished = asyncio.run(
        kernel.refresh_run(
            command_id="cmd-refresh",
            task_id="task-demo",
            run_id=run.run_id,
        )
    )
    asyncio.run(
        kernel.attach_artifact(
            command_id="cmd-artifact",
            task_id="task-demo",
            artifact_ref="artifact-demo",
        )
    )
    task = asyncio.run(
        kernel.record_result(
            command_id="cmd-result",
            task_id="task-demo",
            result_ref="result-demo",
        )
    )

    assert finished.status is ExecutionStatus.SUCCEEDED
    assert finished.output == {"answer": "done"}
    assert task.status is TaskStatus.SUCCEEDED
    assert task.title == "Updated title"
    assert task.run_ids == (run.run_id,)
    assert task.artifact_refs == ("artifact-demo",)
    assert task.result_refs == ("result-demo",)

    history = asyncio.run(kernel.history("task-demo"))
    assert reduce_task(history, "task-demo") == task
    assert reduce_run(history, run.run_id) == finished
    assert all(event.context.correlation_id == "task-demo" for event in history)


def test_start_command_is_idempotent_and_does_not_create_second_run() -> None:
    lifecycle = ControllableLifecycle()
    kernel = _kernel(lifecycle)
    _create_ready(kernel)

    first = asyncio.run(kernel.start_task(command_id="cmd-start", task_id="task-demo"))
    second = asyncio.run(kernel.start_task(command_id="cmd-start", task_id="task-demo"))
    task = asyncio.run(kernel.get_task("task-demo"))

    assert second.run_id == first.run_id
    assert task.run_ids == (first.run_id,)
    assert lifecycle.start_calls == [first.run_id]


def test_cancel_run_transitions_run_and_task_deterministically() -> None:
    lifecycle = ControllableLifecycle()
    kernel = _kernel(lifecycle)
    _create_ready(kernel)
    run = asyncio.run(kernel.start_task(command_id="cmd-start", task_id="task-demo"))

    cancelled = asyncio.run(
        kernel.cancel_run(
            command_id="cmd-cancel",
            task_id="task-demo",
            run_id=run.run_id,
        )
    )
    task = asyncio.run(kernel.get_task("task-demo"))

    assert cancelled.status is ExecutionStatus.CANCELLED
    assert task.status is TaskStatus.CANCELLED


def test_invalid_task_transition_is_rejected() -> None:
    kernel = _kernel()
    _create_ready(kernel)

    with pytest.raises(ContractError) as error:
        asyncio.run(kernel.ready_task(command_id="cmd-ready-again", task_id="task-demo"))

    assert error.value.code is ErrorCode.CONFLICT


def test_restart_recovers_backend_run_without_duplicate_start(tmp_path: Path) -> None:
    database = tmp_path / "events.sqlite3"
    lifecycle = CrashAfterBackendStart()
    first_events = SqliteEventProvider(database)
    first_kernel = _kernel(lifecycle, first_events)
    _create_ready(first_kernel)

    with pytest.raises(ContractError) as error:
        asyncio.run(first_kernel.start_task(command_id="cmd-start", task_id="task-demo"))
    assert error.value.code is ErrorCode.UNAVAILABLE

    before_restart = asyncio.run(first_kernel.get_task("task-demo"))
    assert len(before_restart.run_ids) == 1
    run_id = before_restart.run_ids[0]
    assert lifecycle.start_calls == [run_id]

    # A new provider and kernel instance represent a restarted platform process.
    second_events = SqliteEventProvider(database)
    second_kernel = _kernel(lifecycle, second_events)
    recovered_task = asyncio.run(second_kernel.recover_task("task-demo"))
    recovered_run = asyncio.run(second_kernel.get_run("task-demo", run_id))

    assert recovered_task.run_ids == (run_id,)
    assert recovered_task.status is TaskStatus.RUNNING
    assert recovered_run.status is ExecutionStatus.RUNNING
    assert lifecycle.start_calls == [run_id]

    # Replaying the original command also resolves to the same canonical run.
    same_run = asyncio.run(second_kernel.start_task(command_id="cmd-start", task_id="task-demo"))
    assert same_run.run_id == run_id
    assert lifecycle.start_calls == [run_id]


def test_sqlite_history_survives_provider_reconstruction(tmp_path: Path) -> None:
    database = tmp_path / "events.sqlite3"
    first_kernel = _kernel(ControllableLifecycle(), SqliteEventProvider(database))
    _create_ready(first_kernel)
    task_before = asyncio.run(first_kernel.get_task("task-demo"))

    second_kernel = _kernel(ControllableLifecycle(), SqliteEventProvider(database))
    task_after = asyncio.run(second_kernel.get_task("task-demo"))

    assert task_after == task_before
    history = asyncio.run(second_kernel.history("task-demo"))
    assert [event.event_type for event in history] == [
        "task.created",
        "task.updated",
        "task.ready",
    ]
