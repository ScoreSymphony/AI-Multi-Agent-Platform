from pathlib import Path

kernel_path = Path("src/ai_multi_agent_platform/kernel/kernel.py")
text = kernel_path.read_text()

start = text.index("    async def cancel_task(\n")
end = text.index("    async def plan_task(\n", start)
replacement = '''    async def cancel_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = _KERNEL_SOURCE,
    ) -> TaskState:
        task = await self.get_task(task_id)
        if await self._task_command(task_id, idempotency_key, "cancel_task") is not None:
            return await self.get_task(task_id)
        if task.status is TaskStatus.SUCCEEDED:
            raise ContractError(ErrorCode.CONFLICT, f"task {task_id} already succeeded")
        if task.status is TaskStatus.FAILED:
            raise ContractError(
                ErrorCode.CONFLICT,
                "canonical lifecycle requires failed tasks to be retried to ready "
                "rather than cancelled",
            )
        if task.status not in {
            TaskStatus.DRAFT,
            TaskStatus.READY,
            TaskStatus.RUNNING,
            TaskStatus.WAITING,
            TaskStatus.CANCELLED,
        }:
            raise ContractError(ErrorCode.CONFLICT, f"task {task_id} cannot be cancelled")

        active_runs = await self._active_runs(task)
        step_runs = tuple(run for run in active_runs if run.run.subject_type == "step")
        task_runs = tuple(run for run in active_runs if run.run.subject_type == "task")
        for run in (*step_runs, *task_runs):
            await self.cancel_run(
                idempotency_key=f"{idempotency_key}:run:{run.run_id}",
                task_id=task_id,
                run_id=run.run_id,
                actor_ref=actor_ref,
                source=source,
            )

        refreshed = await self.get_task(task_id)
        remaining = await self._active_runs(refreshed)
        if remaining:
            remaining_ids = ", ".join(run.run_id for run in remaining)
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cancellation is incomplete; active runs: {remaining_ids}",
            )

        if refreshed.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}:
            await self._commit_task_command(
                task=refreshed,
                key=idempotency_key,
                operation="cancel_task",
                event_specs=(
                    (
                        "task.cancel_lost_race",
                        "task",
                        task_id,
                        {"status": refreshed.status.value},
                        (),
                    ),
                ),
                result_id=task_id,
                actor_ref=actor_ref,
                source=source,
            )
            return await self.get_task(task_id)

        event_type = (
            "task.cancel_acknowledged"
            if refreshed.status is TaskStatus.CANCELLED
            else "task.cancelled"
        )
        await self._commit_task_command(
            task=refreshed,
            key=idempotency_key,
            operation="cancel_task",
            event_specs=((event_type, "task", task_id, {}, ()),),
            result_id=task_id,
            actor_ref=actor_ref,
            source=source,
        )
        return await self.get_task(task_id)

'''
text = text[:start] + replacement + text[end:]

old_create = '''        if task.status not in allowed_task_statuses:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot create a {subject_type} run from {task.status.value}",\n            )\n        active_subject_run = await self._active_run_for_subject(\n'''
new_create = '''        if task.status not in allowed_task_statuses:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot create a {subject_type} run from {task.status.value}",\n            )\n        active_runs = await self._active_runs(task)\n        active_modes = {active.run.subject_type for active in active_runs}\n        if active_modes and subject_type not in active_modes:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                "task-level and step-level runs cannot be active at the same time",\n            )\n        active_subject_run = await self._active_run_for_subject(\n'''
if text.count(old_create) != 1:
    raise SystemExit("create_run insertion target mismatch")
text = text.replace(old_create, new_create, 1)

old_start = '''        if task.status not in allowed_task_statuses:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot start a {run.run.subject_type} run "\n                f"from {task.status.value}",\n            )\n        if run.status is not RunStatus.QUEUED:\n'''
new_start = '''        if task.status not in allowed_task_statuses:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot start a {run.run.subject_type} run "\n                f"from {task.status.value}",\n            )\n        other_active_runs = tuple(\n            active for active in await self._active_runs(task) if active.run_id != run_id\n        )\n        if any(\n            active.run.subject_type != run.run.subject_type for active in other_active_runs\n        ):\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                "task-level and step-level runs cannot be active at the same time",\n            )\n        if run.status is not RunStatus.QUEUED:\n'''
if text.count(old_start) != 1:
    raise SystemExit("start_run insertion target mismatch")
text = text.replace(old_start, new_start, 1)

marker = "    async def _active_run_for_subject(\n"
helper = '''    async def _active_runs(self, task: TaskState) -> tuple[RunState, ...]:
        active: list[RunState] = []
        for run_id in task.run_ids:
            run = await self.get_run(task.task_id, run_id)
            if run.status not in TERMINAL_RUN_STATUSES:
                active.append(run)
        return tuple(active)

'''
if helper not in text:
    if text.count(marker) != 1:
        raise SystemExit("active-run helper insertion target mismatch")
    text = text.replace(marker, helper + marker, 1)

kernel_path.write_text(text)

test_path = Path("tests/test_kernel_consistency.py")
tests = test_path.read_text()
addition = '''


def test_cancel_task_cancels_all_parallel_step_runs() -> None:
    lifecycle = FakeLifecycleBackend()
    k = make_kernel(lifecycle)
    task_id = ready(k, "parallel")
    step_a = new_id("step")
    step_b = new_id("step")
    run_a = asyncio.run(
        k.create_run(
            idempotency_key="parallel:a:create",
            task_id=task_id,
            subject_type="step",
            subject_id=step_a,
        )
    )
    run_b = asyncio.run(
        k.create_run(
            idempotency_key="parallel:b:create",
            task_id=task_id,
            subject_type="step",
            subject_id=step_b,
        )
    )
    asyncio.run(
        k.start_run(idempotency_key="parallel:a:start", task_id=task_id, run_id=run_a.run_id)
    )
    asyncio.run(
        k.start_run(idempotency_key="parallel:b:start", task_id=task_id, run_id=run_b.run_id)
    )

    task = asyncio.run(k.cancel_task(idempotency_key="parallel:cancel", task_id=task_id))

    assert task.status is TaskStatus.CANCELLED
    assert asyncio.run(k.get_run(task_id, run_a.run_id)).status is RunStatus.CANCELLED
    assert asyncio.run(k.get_run(task_id, run_b.run_id)).status is RunStatus.CANCELLED
    assert {call[0] for call in lifecycle.cancel_calls} == {run_a.run_id, run_b.run_id}


def test_cancel_task_handles_queued_and_running_step_runs() -> None:
    lifecycle = FakeLifecycleBackend()
    k = make_kernel(lifecycle)
    task_id = ready(k, "mixed-state")
    queued = asyncio.run(
        k.create_run(
            idempotency_key="mixed-state:queued:create",
            task_id=task_id,
            subject_type="step",
            subject_id=new_id("step"),
        )
    )
    running = asyncio.run(
        k.create_run(
            idempotency_key="mixed-state:running:create",
            task_id=task_id,
            subject_type="step",
            subject_id=new_id("step"),
        )
    )
    asyncio.run(
        k.start_run(
            idempotency_key="mixed-state:running:start",
            task_id=task_id,
            run_id=running.run_id,
        )
    )

    task = asyncio.run(k.cancel_task(idempotency_key="mixed-state:cancel", task_id=task_id))

    assert task.status is TaskStatus.CANCELLED
    assert asyncio.run(k.get_run(task_id, queued.run_id)).status is RunStatus.CANCELLED
    assert asyncio.run(k.get_run(task_id, running.run_id)).status is RunStatus.CANCELLED
    assert [call[0] for call in lifecycle.cancel_calls] == [running.run_id]


def test_cancel_task_is_idempotent_after_parallel_cancellation() -> None:
    lifecycle = FakeLifecycleBackend()
    k = make_kernel(lifecycle)
    task_id = ready(k, "repeat")
    runs = []
    for index in range(2):
        run = asyncio.run(
            k.create_run(
                idempotency_key=f"repeat:{index}:create",
                task_id=task_id,
                subject_type="step",
                subject_id=new_id("step"),
            )
        )
        asyncio.run(
            k.start_run(
                idempotency_key=f"repeat:{index}:start",
                task_id=task_id,
                run_id=run.run_id,
            )
        )
        runs.append(run)

    first = asyncio.run(k.cancel_task(idempotency_key="repeat:cancel", task_id=task_id))
    call_count = len(lifecycle.cancel_calls)
    second = asyncio.run(k.cancel_task(idempotency_key="repeat:cancel", task_id=task_id))
    third = asyncio.run(k.cancel_task(idempotency_key="repeat:cancel-again", task_id=task_id))

    assert first.status is TaskStatus.CANCELLED
    assert second.status is TaskStatus.CANCELLED
    assert third.status is TaskStatus.CANCELLED
    assert len(lifecycle.cancel_calls) == call_count == 2
    assert all(
        asyncio.run(k.get_run(task_id, run.run_id)).status is RunStatus.CANCELLED
        for run in runs
    )


def test_task_and_step_execution_modes_cannot_be_mixed() -> None:
    k = make_kernel()
    task_id = ready(k, "mode")
    task_run = asyncio.run(k.create_run(idempotency_key="mode:task", task_id=task_id))

    with pytest.raises(ContractError):
        asyncio.run(
            k.create_run(
                idempotency_key="mode:step",
                task_id=task_id,
                subject_type="step",
                subject_id=new_id("step"),
            )
        )

    assert task_run.run.subject_type == "task"


def test_recovery_after_parallel_cancellation_keeps_runs_terminal() -> None:
    lifecycle = FakeLifecycleBackend()
    k = make_kernel(lifecycle)
    task_id = ready(k, "recovery-cancel")
    runs = []
    for index in range(2):
        run = asyncio.run(
            k.create_run(
                idempotency_key=f"recovery-cancel:{index}:create",
                task_id=task_id,
                subject_type="step",
                subject_id=new_id("step"),
            )
        )
        asyncio.run(
            k.start_run(
                idempotency_key=f"recovery-cancel:{index}:start",
                task_id=task_id,
                run_id=run.run_id,
            )
        )
        runs.append(run)

    asyncio.run(k.cancel_task(idempotency_key="recovery-cancel:cancel", task_id=task_id))
    calls_before_recovery = len(lifecycle.start_calls)
    report = asyncio.run(k.recover_task(task_id))

    assert asyncio.run(k.get_task(task_id)).status is TaskStatus.CANCELLED
    assert len(report.entries) == 2
    assert all(entry.after is RunStatus.CANCELLED for entry in report.entries)
    assert len(lifecycle.start_calls) == calls_before_recovery
'''
if "def test_cancel_task_cancels_all_parallel_step_runs" not in tests:
    test_path.write_text(tests + addition)

docs_path = Path("docs/KERNEL.md")
docs = docs_path.read_text()
old_docs = (
    "Cancelling the parent task still cancels an active step run first and then "
    "terminalizes the task. A second non-terminal run for the same subject is rejected."
)
new_docs = (
    "Cancelling the parent task cancels every non-terminal run first (queued runs "
    "canonically, starting/running runs through the lifecycle backend) and only then "
    "terminalizes the task. Parallel step runs are supported for distinct step subjects, "
    "but task-level and step-level execution modes cannot be active at the same time. "
    "A second non-terminal run for the same subject is rejected."
)
if old_docs not in docs:
    raise SystemExit("KERNEL.md cancellation text mismatch")
docs_path.write_text(docs.replace(old_docs, new_docs, 1))
