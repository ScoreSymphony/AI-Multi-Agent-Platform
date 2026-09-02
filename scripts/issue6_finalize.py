from __future__ import annotations

from pathlib import Path


kernel_path = Path("src/ai_multi_agent_platform/kernel/kernel.py")
text = kernel_path.read_text()


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected exactly one replacement target, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    '''        if task.status is not TaskStatus.RUNNING:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot succeed from {task.status.value}",\n            )\n        await self._commit_task_command(\n''',
    '''        if task.status is not TaskStatus.RUNNING:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot succeed from {task.status.value}",\n            )\n        active = await self._latest_active_run(task)\n        if active is not None:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot succeed while run {active.run_id} is {active.status.value}",\n            )\n        await self._commit_task_command(\n''',
)

replace_once(
    '''        if task.status not in {TaskStatus.RUNNING, TaskStatus.WAITING}:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot fail from {task.status.value}",\n            )\n        payload: dict[str, JsonValue] = {}\n''',
    '''        if task.status not in {TaskStatus.RUNNING, TaskStatus.WAITING}:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot fail from {task.status.value}",\n            )\n        active = await self._latest_active_run(task)\n        if active is not None:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot fail while run {active.run_id} is {active.status.value}",\n            )\n        payload: dict[str, JsonValue] = {}\n''',
)

replace_once(
    '''            if refreshed.status is TaskStatus.CANCELLED:\n                await self._commit_task_command(\n                    task=refreshed,\n                    key=idempotency_key,\n                    operation="cancel_task",\n                    event_specs=(("task.cancel_acknowledged", "task", task_id, {}, ()),),\n                    result_id=task_id,\n                    actor_ref=actor_ref,\n                    source=source,\n                )\n                return await self.get_task(task_id)\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"run cancellation completed as {refreshed.status.value}, not task cancellation",\n            )\n''',
    '''            if refreshed.status is TaskStatus.CANCELLED:\n                await self._commit_task_command(\n                    task=refreshed,\n                    key=idempotency_key,\n                    operation="cancel_task",\n                    event_specs=(("task.cancel_acknowledged", "task", task_id, {}, ()),),\n                    result_id=task_id,\n                    actor_ref=actor_ref,\n                    source=source,\n                )\n                return await self.get_task(task_id)\n            if active.run.subject_type == "step":\n                await self._commit_task_command(\n                    task=refreshed,\n                    key=idempotency_key,\n                    operation="cancel_task",\n                    event_specs=(("task.cancelled", "task", task_id, {}, ()),),\n                    result_id=task_id,\n                    actor_ref=actor_ref,\n                    source=source,\n                )\n                return await self.get_task(task_id)\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"run cancellation completed as {refreshed.status.value}, not task cancellation",\n            )\n''',
)

replace_once(
    '''        if task.status is not TaskStatus.READY:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot create a run from {task.status.value}",\n            )\n        canonical_subject_id = subject_id or task_id\n        validate_subject_id(subject_type, canonical_subject_id)\n        run_id = new_id("run")\n        attempt = len(task.run_ids) + 1\n''',
    '''        canonical_subject_id = subject_id or task_id\n        validate_subject_id(subject_type, canonical_subject_id)\n        allowed_task_statuses = (\n            {TaskStatus.READY}\n            if subject_type == "task"\n            else {TaskStatus.READY, TaskStatus.RUNNING}\n        )\n        if task.status not in allowed_task_statuses:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot create a {subject_type} run from {task.status.value}",\n            )\n        active_subject_run = await self._active_run_for_subject(\n            task, subject_type, canonical_subject_id\n        )\n        if active_subject_run is not None:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"subject {canonical_subject_id} already has active run "\n                f"{active_subject_run.run_id}",\n            )\n        run_id = new_id("run")\n        attempt = await self._next_attempt(task, subject_type, canonical_subject_id)\n''',
)

replace_once(
    '''        if task.status is not TaskStatus.FAILED:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot retry from {task.status.value}",\n            )\n        run_id = new_id("run")\n        attempt = len(task.run_ids) + 1\n''',
    '''        if task.status is not TaskStatus.FAILED:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot retry from {task.status.value}",\n            )\n        active = await self._latest_active_run(task)\n        if active is not None:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot retry while run {active.run_id} is {active.status.value}",\n            )\n        run_id = new_id("run")\n        attempt = await self._next_attempt(task, "task", task_id)\n''',
)

replace_once(
    '''        if task.status is not TaskStatus.READY:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot start a run from {task.status.value}",\n            )\n        if run.status is not RunStatus.QUEUED:\n''',
    '''        allowed_task_statuses = (\n            {TaskStatus.READY}\n            if run.run.subject_type == "task"\n            else {TaskStatus.READY, TaskStatus.RUNNING}\n        )\n        if task.status not in allowed_task_statuses:\n            raise ContractError(\n                ErrorCode.CONFLICT,\n                f"task {task_id} cannot start a {run.run.subject_type} run "\n                f"from {task.status.value}",\n            )\n        if run.status is not RunStatus.QUEUED:\n''',
)

replace_once(
    '''        event_type = f"run.{target.value}"\n        specs.append((event_type, "run", run.run_id, {"output": output}, adapter_metadata))\n\n        task_status = task.status\n''',
    '''        event_type = f"run.{target.value}"\n        specs.append((event_type, "run", run.run_id, {"output": output}, adapter_metadata))\n\n        if run.run.subject_type == "step":\n            return tuple(specs)\n\n        task_status = task.status\n''',
)

replace_once(
    '''    async def _latest_active_run(self, task: TaskState) -> RunState | None:\n''',
    '''    async def _active_run_for_subject(\n        self,\n        task: TaskState,\n        subject_type: RunSubjectType,\n        subject_id: str,\n    ) -> RunState | None:\n        for run_id in reversed(task.run_ids):\n            run = await self.get_run(task.task_id, run_id)\n            if (\n                run.run.subject_type == subject_type\n                and run.run.subject_id == subject_id\n                and run.status not in TERMINAL_RUN_STATUSES\n            ):\n                return run\n        return None\n\n    async def _next_attempt(\n        self,\n        task: TaskState,\n        subject_type: RunSubjectType,\n        subject_id: str,\n    ) -> int:\n        latest = 0\n        for run_id in task.run_ids:\n            run = await self.get_run(task.task_id, run_id)\n            if run.run.subject_type == subject_type and run.run.subject_id == subject_id:\n                latest = max(latest, run.run.attempt)\n        return latest + 1\n\n    async def _latest_active_run(self, task: TaskState) -> RunState | None:\n''',
)

kernel_path.write_text(text)

Path("tests/test_kernel_consistency.py").write_text(
    '''from __future__ import annotations\n\nimport asyncio\n\nimport pytest\n\nfrom ai_multi_agent_platform.contracts import ContractError, ExecutionStatus\nfrom ai_multi_agent_platform.domain import RunStatus, TaskStatus, new_id\nfrom ai_multi_agent_platform.kernel import PlatformKernel\nfrom ai_multi_agent_platform.testing import FakeOrchestrator\nfrom ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend\n\n\ndef make_kernel(lifecycle: FakeLifecycleBackend | None = None) -> PlatformKernel:\n    return PlatformKernel(\n        orchestrator=FakeOrchestrator(),\n        lifecycle=lifecycle or FakeLifecycleBackend(),\n    )\n\n\ndef ready(k: PlatformKernel, key: str = "create") -> str:\n    task = asyncio.run(\n        k.create_task(\n            idempotency_key=key,\n            title="Consistency task",\n            objective="Protect canonical task/run consistency",\n            owner_type="user",\n            owner_id="test-owner",\n        )\n    )\n    asyncio.run(k.ready_task(idempotency_key=f"{key}:ready", task_id=task.task_id))\n    return task.task_id\n\n\ndef test_direct_task_terminalization_rejects_active_run() -> None:\n    lifecycle = FakeLifecycleBackend()\n    k = make_kernel(lifecycle)\n    task_id = ready(k)\n    run = asyncio.run(k.start_task(idempotency_key="start", task_id=task_id))\n\n    with pytest.raises(ContractError):\n        asyncio.run(k.complete_task(idempotency_key="complete", task_id=task_id))\n    with pytest.raises(ContractError):\n        asyncio.run(k.fail_task(idempotency_key="fail", task_id=task_id))\n\n    assert asyncio.run(k.get_task(task_id)).status is TaskStatus.RUNNING\n    assert asyncio.run(k.get_run(task_id, run.run_id)).status is RunStatus.RUNNING\n\n\ndef test_step_runs_have_subject_scoped_attempts_and_do_not_terminalize_task() -> None:\n    lifecycle = FakeLifecycleBackend()\n    k = make_kernel(lifecycle)\n    task_id = ready(k)\n    step_a = new_id("step")\n    step_b = new_id("step")\n\n    run_a1 = asyncio.run(\n        k.create_run(\n            idempotency_key="step-a-1",\n            task_id=task_id,\n            subject_type="step",\n            subject_id=step_a,\n        )\n    )\n    run_b1 = asyncio.run(\n        k.create_run(\n            idempotency_key="step-b-1",\n            task_id=task_id,\n            subject_type="step",\n            subject_id=step_b,\n        )\n    )\n    assert run_a1.attempt == 1\n    assert run_b1.attempt == 1\n\n    with pytest.raises(ContractError):\n        asyncio.run(\n            k.create_run(\n                idempotency_key="step-a-duplicate-active",\n                task_id=task_id,\n                subject_type="step",\n                subject_id=step_a,\n            )\n        )\n\n    asyncio.run(k.start_run(idempotency_key="start-a-1", task_id=task_id, run_id=run_a1.run_id))\n    lifecycle.complete(run_a1.run_id, status=ExecutionStatus.SUCCEEDED)\n    finished = asyncio.run(\n        k.refresh_run(idempotency_key="finish-a-1", task_id=task_id, run_id=run_a1.run_id)\n    )\n    assert finished.status is RunStatus.SUCCEEDED\n    assert asyncio.run(k.get_task(task_id)).status is TaskStatus.RUNNING\n\n    run_a2 = asyncio.run(\n        k.create_run(\n            idempotency_key="step-a-2",\n            task_id=task_id,\n            subject_type="step",\n            subject_id=step_a,\n        )\n    )\n    assert run_a2.attempt == 2\n\n\ndef test_cancelling_task_with_active_step_run_cancels_both() -> None:\n    lifecycle = FakeLifecycleBackend()\n    k = make_kernel(lifecycle)\n    task_id = ready(k)\n    step_id = new_id("step")\n    run = asyncio.run(\n        k.create_run(\n            idempotency_key="step",\n            task_id=task_id,\n            subject_type="step",\n            subject_id=step_id,\n        )\n    )\n    asyncio.run(k.start_run(idempotency_key="start-step", task_id=task_id, run_id=run.run_id))\n\n    task = asyncio.run(k.cancel_task(idempotency_key="cancel-task", task_id=task_id))\n    assert task.status is TaskStatus.CANCELLED\n    assert asyncio.run(k.get_run(task_id, run.run_id)).status is RunStatus.CANCELLED\n    assert len(lifecycle.cancel_calls) == 1\n'''
)

docs_path = Path("docs/KERNEL.md")
docs = docs_path.read_text()
marker = "## Event history and read models\n"
addition = '''### Task/run consistency and step-run semantics\n\nDirect `complete_task()` and `fail_task()` operations are rejected while any canonical run is still non-terminal. Task terminal state therefore cannot diverge from an active run, and task retry is likewise rejected while unfinished work remains.\n\nAttempts are scoped to the canonical run subject `(subject_type, subject_id)`, not to the task-wide number of runs. Task runs may be created/started only while the task is `ready`. Step runs may be created/started while the task is `ready` or `running`; their success, failure, timeout or cancellation terminalizes the step run only and does not implicitly terminalize the parent task. Cancelling the parent task still cancels an active step run first and then terminalizes the task. A second non-terminal run for the same subject is rejected.\n\n'''
if addition not in docs:
    if marker not in docs:
        raise SystemExit("KERNEL.md insertion marker missing")
    docs_path.write_text(docs.replace(marker, addition + marker, 1))

Path(".github/workflows/issue6-format.yml").unlink(missing_ok=True)
Path(".github/workflows/issue6-final-consistency.yml").unlink(missing_ok=True)
Path("scripts/issue6_finalize.py").unlink(missing_ok=True)
