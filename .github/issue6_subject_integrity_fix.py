from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text()
    if text.count(old) != 1:
        raise SystemExit(f"replacement target mismatch in {path}: {text.count(old)} matches")
    target.write_text(text.replace(old, new, 1))


# Kernel: prevent replanning while canonical work is active.
replace_once(
    "src/ai_multi_agent_platform/kernel/kernel.py",
    '''        if task.status in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}:
            raise ContractError(ErrorCode.CONFLICT, f"task {task_id} is terminal")

        context = self._context(task, idempotency_key)
''',
    '''        if task.status in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}:
            raise ContractError(ErrorCode.CONFLICT, f"task {task_id} is terminal")
        active = await self._latest_active_run(task)
        if active is not None:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task_id} cannot be replanned while run "
                f"{active.run_id} is {active.status.value}",
            )

        context = self._context(task, idempotency_key)
''',
)

# Kernel: validate the canonical subject relationship before run.created is persisted.
replace_once(
    "src/ai_multi_agent_platform/kernel/kernel.py",
    '''        canonical_subject_id = subject_id or task_id
        validate_subject_id(subject_type, canonical_subject_id)
        allowed_task_statuses = (
''',
    '''        canonical_subject_id = subject_id or task_id
        validate_subject_id(subject_type, canonical_subject_id)
        self._validate_run_subject(task, subject_type, canonical_subject_id)
        allowed_task_statuses = (
''',
)

replace_once(
    "src/ai_multi_agent_platform/kernel/kernel.py",
    '''    async def _active_runs(self, task: TaskState) -> tuple[RunState, ...]:
''',
    '''    @staticmethod
    def _validate_run_subject(
        task: TaskState,
        subject_type: RunSubjectType,
        subject_id: str,
    ) -> None:
        if subject_type == "task":
            if subject_id != task.task_id:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"task run subject {subject_id} does not match owning task {task.task_id}",
                )
            return
        if task.plan_ref is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task.task_id} has no canonical plan for step run {subject_id}",
            )
        if subject_id not in task.step_ids:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"step {subject_id} does not belong to current plan {task.plan_ref} "
                f"for task {task.task_id}",
            )

    async def _active_runs(self, task: TaskState) -> tuple[RunState, ...]:
''',
)

# TaskState: expose only the current canonical plan's Step identities.
replace_once(
    "src/ai_multi_agent_platform/kernel/models.py",
    '''    plan_ref: str | None = None
    run_ids: tuple[str, ...] = ()
''',
    '''    plan_ref: str | None = None
    step_ids: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()
''',
)

# Reducer: reconstruct current-plan Step identities from canonical plan.created events.
replace_once(
    "src/ai_multi_agent_platform/kernel/state.py",
    '''    TaskStatus,
    require_transition,
)
''',
    '''    TaskStatus,
    require_transition,
    validate_id,
)
''',
)
replace_once(
    "src/ai_multi_agent_platform/kernel/state.py",
    '''    plan_ref: str | None = None
    run_ids: list[str] = []
''',
    '''    plan_ref: str | None = None
    step_ids: tuple[str, ...] = ()
    run_ids: list[str] = []
''',
)
replace_once(
    "src/ai_multi_agent_platform/kernel/state.py",
    '''        if event.event_type == "plan.created" and event.subject_id == task_id:
            plan_ref = _string(event, "plan_ref")
            continue
''',
    '''        if event.event_type == "plan.created" and event.subject_id == task_id:
            plan_ref = _string(event, "plan_ref")
            raw_step_refs = event.payload.get("step_refs", ())
            if not isinstance(raw_step_refs, (list, tuple)):
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "plan.created step_refs must be a sequence",
                )
            validated_step_ids: list[str] = []
            for step_id in raw_step_refs:
                if not isinstance(step_id, str):
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "plan.created step_refs must contain canonical Step IDs",
                    )
                try:
                    validate_id(step_id, "step")
                except ValueError as exc:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "plan.created contains an invalid canonical Step ID",
                    ) from exc
                validated_step_ids.append(step_id)
            step_ids = tuple(validated_step_ids)
            continue
''',
)
replace_once(
    "src/ai_multi_agent_platform/kernel/state.py",
    '''        plan_ref=plan_ref,
        run_ids=tuple(run_ids),
''',
    '''        plan_ref=plan_ref,
        step_ids=step_ids,
        run_ids=tuple(run_ids),
''',
)

# Existing kernel test: use a real canonical Step instead of a fabricated Step-shaped ID.
replace_once(
    "tests/test_kernel.py",
    '''    other = kernel()
    other_task = ready(other, "other")
    step_id = new_id("step")
    step_run = asyncio.run(
''',
    '''    other = kernel()
    other_task = ready(other, "other")
    planned = asyncio.run(other.plan_task(idempotency_key="other:plan", task_id=other_task))
    assert planned.step_ids
    step_id = planned.step_ids[0]
    step_run = asyncio.run(
''',
)

# Consistency tests: every Step Run now targets Steps allocated by the platform plan.
replace_once(
    "tests/test_kernel_consistency.py",
    '''from ai_multi_agent_platform.contracts import ContractError, ExecutionStatus
from ai_multi_agent_platform.domain import RunStatus, TaskStatus, new_id
''',
    '''from ai_multi_agent_platform.contracts import (
    ContractError,
    ExecutionStatus,
    PlanRequest,
    PlanResponse,
    PlanStepProposal,
)
from ai_multi_agent_platform.domain import RunStatus, TaskStatus
''',
)
replace_once(
    "tests/test_kernel_consistency.py",
    '''def make_kernel(lifecycle: FakeLifecycleBackend | None = None) -> PlatformKernel:
    return PlatformKernel(
        orchestrator=FakeOrchestrator(),
''',
    '''class ParallelFakeOrchestrator(FakeOrchestrator):
    async def plan(self, request: PlanRequest) -> PlanResponse:
        self.calls.append(request)
        return PlanResponse(
            summary=f"Plan for {request.objective}",
            steps=(
                PlanStepProposal(key="step-a", title="Step A", objective=request.objective),
                PlanStepProposal(key="step-b", title="Step B", objective=request.objective),
            ),
        )


def make_kernel(lifecycle: FakeLifecycleBackend | None = None) -> PlatformKernel:
    return PlatformKernel(
        orchestrator=ParallelFakeOrchestrator(),
''',
)
replace_once(
    "tests/test_kernel_consistency.py",
    '''    asyncio.run(k.ready_task(idempotency_key=f"{key}:ready", task_id=task.task_id))
    return task.task_id


def test_direct_task_terminalization_rejects_active_run() -> None:
''',
    '''    asyncio.run(k.ready_task(idempotency_key=f"{key}:ready", task_id=task.task_id))
    planned = asyncio.run(
        k.plan_task(idempotency_key=f"{key}:plan", task_id=task.task_id)
    )
    assert len(planned.step_ids) == 2
    return task.task_id


def planned_step_ids(k: PlatformKernel, task_id: str) -> tuple[str, str]:
    task = asyncio.run(k.get_task(task_id))
    assert len(task.step_ids) == 2
    return task.step_ids[0], task.step_ids[1]


def test_direct_task_terminalization_rejects_active_run() -> None:
''',
)
# Replace two-step local allocations (two occurrences).
text_path = Path("tests/test_kernel_consistency.py")
text = text_path.read_text()
old_pair = '''    step_a = new_id("step")
    step_b = new_id("step")
'''
if text.count(old_pair) != 2:
    raise SystemExit(f"expected two paired step allocations, found {text.count(old_pair)}")
text = text.replace(old_pair, '''    step_a, step_b = planned_step_ids(k, task_id)
''')
# Replace the single-step cancellation allocation.
old_single = '''    step_id = new_id("step")
'''
if text.count(old_single) != 1:
    raise SystemExit(f"expected one single step allocation, found {text.count(old_single)}")
text = text.replace(old_single, '''    step_id = planned_step_ids(k, task_id)[0]
''', 1)
# Mixed queued/running test.
old_mixed = '''    queued = asyncio.run(
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
'''
new_mixed = '''    queued_step, running_step = planned_step_ids(k, task_id)
    queued = asyncio.run(
        k.create_run(
            idempotency_key="mixed-state:queued:create",
            task_id=task_id,
            subject_type="step",
            subject_id=queued_step,
        )
    )
    running = asyncio.run(
        k.create_run(
            idempotency_key="mixed-state:running:create",
            task_id=task_id,
            subject_type="step",
            subject_id=running_step,
        )
    )
'''
if text.count(old_mixed) != 1:
    raise SystemExit("mixed-state allocation target mismatch")
text = text.replace(old_mixed, new_mixed, 1)
# Repeat-cancellation test loop.
old_repeat = '''    runs = []
    for index in range(2):
        run = asyncio.run(
            k.create_run(
                idempotency_key=f"repeat:{index}:create",
                task_id=task_id,
                subject_type="step",
                subject_id=new_id("step"),
            )
        )
'''
new_repeat = '''    runs = []
    step_ids = planned_step_ids(k, task_id)
    for index in range(2):
        run = asyncio.run(
            k.create_run(
                idempotency_key=f"repeat:{index}:create",
                task_id=task_id,
                subject_type="step",
                subject_id=step_ids[index],
            )
        )
'''
if text.count(old_repeat) != 1:
    raise SystemExit("repeat allocation target mismatch")
text = text.replace(old_repeat, new_repeat, 1)
# Recovery test loop.
old_recovery = '''    runs = []
    for index in range(2):
        run = asyncio.run(
            k.create_run(
                idempotency_key=f"recovery-cancel:{index}:create",
                task_id=task_id,
                subject_type="step",
                subject_id=new_id("step"),
            )
        )
'''
new_recovery = '''    runs = []
    step_ids = planned_step_ids(k, task_id)
    for index in range(2):
        run = asyncio.run(
            k.create_run(
                idempotency_key=f"recovery-cancel:{index}:create",
                task_id=task_id,
                subject_type="step",
                subject_id=step_ids[index],
            )
        )
'''
if text.count(old_recovery) != 1:
    raise SystemExit("recovery allocation target mismatch")
text = text.replace(old_recovery, new_recovery, 1)
if 'new_id("step")' in text:
    raise SystemExit("fabricated Step IDs remain in kernel consistency tests")
text_path.write_text(text)

# New regression coverage for canonical task/plan/step ownership.
Path("tests/test_issue6_subject_integrity.py").write_text('''from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.testing import FakeOrchestrator
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend


def kernel(repository: SqliteKernelRepository | None = None) -> PlatformKernel:
    return PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )


def ready(k: PlatformKernel, key: str) -> str:
    task = asyncio.run(
        k.create_task(
            idempotency_key=f"{key}:create",
            title="Subject integrity",
            objective="Keep canonical run ownership coherent",
            owner_type="user",
            owner_id="subject-integrity-owner",
        )
    )
    asyncio.run(k.ready_task(idempotency_key=f"{key}:ready", task_id=task.task_id))
    return task.task_id


def test_task_run_subject_must_match_owning_task_without_poisoning_history() -> None:
    k = kernel()
    task_a = ready(k, "a")
    task_b = ready(k, "b")
    before = asyncio.run(k.history(task_a))

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            k.create_run(
                idempotency_key="cross-task-run",
                task_id=task_a,
                subject_type="task",
                subject_id=task_b,
            )
        )

    assert exc_info.value.code is ErrorCode.CONFLICT
    assert asyncio.run(k.history(task_a)) == before


def test_step_run_requires_a_step_from_the_tasks_current_plan() -> None:
    k = kernel()
    task_a = ready(k, "a")

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            k.create_run(
                idempotency_key="unplanned-step",
                task_id=task_a,
                subject_type="step",
                subject_id=new_id("step"),
            )
        )
    assert exc_info.value.code is ErrorCode.CONFLICT

    planned_a = asyncio.run(k.plan_task(idempotency_key="a:plan", task_id=task_a))
    assert len(planned_a.step_ids) == 1
    valid_step = planned_a.step_ids[0]
    valid_run = asyncio.run(
        k.create_run(
            idempotency_key="valid-step",
            task_id=task_a,
            subject_type="step",
            subject_id=valid_step,
        )
    )
    assert valid_run.run.subject_id == valid_step

    task_b = ready(k, "b")
    planned_b = asyncio.run(k.plan_task(idempotency_key="b:plan", task_id=task_b))
    foreign_step = planned_b.step_ids[0]
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            k.create_run(
                idempotency_key="foreign-step",
                task_id=task_a,
                subject_type="step",
                subject_id=foreign_step,
            )
        )
    assert exc_info.value.code is ErrorCode.CONFLICT


def test_replanning_is_rejected_while_a_run_from_the_current_plan_is_active() -> None:
    k = kernel()
    task_id = ready(k, "replan")
    planned = asyncio.run(k.plan_task(idempotency_key="replan:plan-1", task_id=task_id))
    run = asyncio.run(
        k.create_run(
            idempotency_key="replan:run",
            task_id=task_id,
            subject_type="step",
            subject_id=planned.step_ids[0],
        )
    )

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(k.plan_task(idempotency_key="replan:plan-2", task_id=task_id))

    assert exc_info.value.code is ErrorCode.CONFLICT
    assert asyncio.run(k.get_run(task_id, run.run_id)).run.subject_id == planned.step_ids[0]


def test_current_plan_step_relationship_survives_sqlite_replay(tmp_path: Path) -> None:
    db = tmp_path / "subject-integrity.sqlite3"
    first = kernel(SqliteKernelRepository(db))
    task_id = ready(first, "replay")
    planned = asyncio.run(first.plan_task(idempotency_key="replay:plan", task_id=task_id))
    step_id = planned.step_ids[0]
    run = asyncio.run(
        first.create_run(
            idempotency_key="replay:run",
            task_id=task_id,
            subject_type="step",
            subject_id=step_id,
        )
    )

    second = kernel(SqliteKernelRepository(db))
    replayed_task = asyncio.run(second.get_task(task_id))
    replayed_run = asyncio.run(second.get_run(task_id, run.run_id))

    assert replayed_task.plan_ref == planned.plan_ref
    assert replayed_task.step_ids == (step_id,)
    assert replayed_run.run.subject_type == "step"
    assert replayed_run.run.subject_id == step_id
''')

# Documentation: record the ownership invariants and replan guard.
replace_once(
    "docs/KERNEL.md",
    '''Attempts are scoped to the canonical run subject `(subject_type, subject_id)`, not to the task-wide number of runs. Task runs may be created/started only while the task is `ready`. Step runs may be created/started while the task is `ready` or `running`; their success, failure, timeout or cancellation terminalizes the step run only and does not implicitly terminalize the parent task. Cancelling the parent task cancels every non-terminal run first (queued runs canonically, starting/running runs through the lifecycle backend) and only then terminalizes the task. Parallel step runs are supported for distinct step subjects, but task-level and step-level execution modes cannot be active at the same time. A second non-terminal run for the same subject is rejected.
''',
    '''Attempts are scoped to the canonical run subject `(subject_type, subject_id)`, not to the task-wide number of runs. A task-level Run must target exactly its owning Task ID; a Run in Task A's stream cannot claim Task B as its subject. Step Runs may target only Step IDs allocated by the current canonical Plan for that Task. `TaskState.step_ids` is reconstructed from the current `plan.created` event, so replay preserves this relationship without a second Step store. Replanning is rejected while any canonical Run is non-terminal, preventing a live Run from being detached from the Plan that owns its Step. Task runs may be created/started only while the task is `ready`. Step runs may be created/started while the task is `ready` or `running`; their success, failure, timeout or cancellation terminalizes the step run only and does not implicitly terminalize the parent task. Cancelling the parent task cancels every non-terminal run first (queued runs canonically, starting/running runs through the lifecycle backend) and only then terminalizes the task. Parallel step runs are supported for distinct Step subjects from the same current Plan, but task-level and step-level execution modes cannot be active at the same time. A second non-terminal run for the same subject is rejected.
''',
)
