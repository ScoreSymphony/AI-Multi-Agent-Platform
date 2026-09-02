from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError, ExecutionStatus
from ai_multi_agent_platform.domain import RunStatus, TaskStatus, new_id
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.testing import FakeOrchestrator
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend


def make_kernel(lifecycle: FakeLifecycleBackend | None = None) -> PlatformKernel:
    return PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=lifecycle or FakeLifecycleBackend(),
    )


def ready(k: PlatformKernel, key: str = "create") -> str:
    task = asyncio.run(
        k.create_task(
            idempotency_key=key,
            title="Consistency task",
            objective="Protect canonical task/run consistency",
            owner_type="user",
            owner_id="test-owner",
        )
    )
    asyncio.run(k.ready_task(idempotency_key=f"{key}:ready", task_id=task.task_id))
    return task.task_id


def test_direct_task_terminalization_rejects_active_run() -> None:
    lifecycle = FakeLifecycleBackend()
    k = make_kernel(lifecycle)
    task_id = ready(k)
    run = asyncio.run(k.start_task(idempotency_key="start", task_id=task_id))

    with pytest.raises(ContractError):
        asyncio.run(k.complete_task(idempotency_key="complete", task_id=task_id))
    with pytest.raises(ContractError):
        asyncio.run(k.fail_task(idempotency_key="fail", task_id=task_id))

    assert asyncio.run(k.get_task(task_id)).status is TaskStatus.RUNNING
    assert asyncio.run(k.get_run(task_id, run.run_id)).status is RunStatus.RUNNING


def test_step_runs_have_subject_scoped_attempts_and_do_not_terminalize_task() -> None:
    lifecycle = FakeLifecycleBackend()
    k = make_kernel(lifecycle)
    task_id = ready(k)
    step_a = new_id("step")
    step_b = new_id("step")

    run_a1 = asyncio.run(
        k.create_run(
            idempotency_key="step-a-1",
            task_id=task_id,
            subject_type="step",
            subject_id=step_a,
        )
    )
    run_b1 = asyncio.run(
        k.create_run(
            idempotency_key="step-b-1",
            task_id=task_id,
            subject_type="step",
            subject_id=step_b,
        )
    )
    assert run_a1.attempt == 1
    assert run_b1.attempt == 1

    with pytest.raises(ContractError):
        asyncio.run(
            k.create_run(
                idempotency_key="step-a-duplicate-active",
                task_id=task_id,
                subject_type="step",
                subject_id=step_a,
            )
        )

    asyncio.run(k.start_run(idempotency_key="start-a-1", task_id=task_id, run_id=run_a1.run_id))
    lifecycle.complete(run_a1.run_id, status=ExecutionStatus.SUCCEEDED)
    finished = asyncio.run(
        k.refresh_run(idempotency_key="finish-a-1", task_id=task_id, run_id=run_a1.run_id)
    )
    assert finished.status is RunStatus.SUCCEEDED
    assert asyncio.run(k.get_task(task_id)).status is TaskStatus.RUNNING

    run_a2 = asyncio.run(
        k.create_run(
            idempotency_key="step-a-2",
            task_id=task_id,
            subject_type="step",
            subject_id=step_a,
        )
    )
    assert run_a2.attempt == 2


def test_cancelling_task_with_active_step_run_cancels_both() -> None:
    lifecycle = FakeLifecycleBackend()
    k = make_kernel(lifecycle)
    task_id = ready(k)
    step_id = new_id("step")
    run = asyncio.run(
        k.create_run(
            idempotency_key="step",
            task_id=task_id,
            subject_type="step",
            subject_id=step_id,
        )
    )
    asyncio.run(k.start_run(idempotency_key="start-step", task_id=task_id, run_id=run.run_id))

    task = asyncio.run(k.cancel_task(idempotency_key="cancel-task", task_id=task_id))
    assert task.status is TaskStatus.CANCELLED
    assert asyncio.run(k.get_run(task_id, run.run_id)).status is RunStatus.CANCELLED
    assert len(lifecycle.cancel_calls) == 1
