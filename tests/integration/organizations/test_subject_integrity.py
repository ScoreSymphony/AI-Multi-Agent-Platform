from __future__ import annotations

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
