from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ExecutionHandle, ExecutionSnapshot, ExecutionStatus
from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    DurablePlanStepCoordinator,
    InMemoryCoordinatorRepository,
)
from ai_multi_agent_platform.distributed import (
    DispatchState,
    DistributedLifecycleBackend,
    DistributedRegistry,
    DistributedRuntime,
    LocalWorker,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import Plan, RunStatus, Step, StepStatus, TaskStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class LostAcknowledgementWorker:
    """Accept one real Worker Job but lose only its dispatch acknowledgement."""

    def __init__(self, worker: LocalWorker) -> None:
        self._worker = worker

    @property
    def worker_id(self) -> str:
        return self._worker.worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        await self._worker.dispatch(job)
        raise RuntimeError("simulated acknowledgement loss")

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        return await self._worker.get(worker_job_id)

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        return await self._worker.cancel(worker_job_id)


def _distributed_runtime(
    lifecycle: FakeLifecycleBackend,
) -> tuple[DistributedRuntime, WorkerRecord, LocalWorker]:
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="issue-384-distributed-node",
        resources=ResourceSnapshot(
            cpu_cores_total=4.0,
            cpu_cores_available=4.0,
            ram_total_bytes=8_000,
            ram_available_bytes=8_000,
            storage_total_bytes=100_000,
            storage_available_bytes=100_000,
        ),
        supported_runtimes=("python",),
    )
    worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        supported_executors=("reference",),
        supported_runtimes=("python",),
        concurrency_limit=1,
    )
    runtime = DistributedRuntime(DistributedRegistry())
    runtime.register(RegistrationRequest(node=node, workers=(worker,)))
    local_worker = LocalWorker(worker.worker_id, lifecycle)
    return runtime, worker, local_worker


async def _canonical_single_step(kernel: PlatformKernel, *, key: str) -> tuple[Plan, Step]:
    project_id = new_id("project")
    created = await kernel.create_task(
        idempotency_key=f"{key}:create",
        title=f"{key} task",
        objective=f"{key} objective",
        owner_type="user",
        owner_id="issue-384-distributed-user",
        project_id=project_id,
    )
    await kernel.ready_task(idempotency_key=f"{key}:ready", task_id=created.task_id)
    planned = await kernel.plan_task(idempotency_key=f"{key}:plan", task_id=created.task_id)
    assert planned.plan_ref is not None
    assert len(planned.step_ids) == 1
    plan = Plan(
        id=planned.plan_ref,
        task_id=planned.task_id,
        owner_ref=planned.task.owner_ref,
        active=True,
        project_id=planned.task.project_id,
    )
    step = Step(
        id=planned.step_ids[0],
        plan_id=plan.id,
        title=f"{key} step",
        owner_ref=planned.task.owner_ref,
        project_id=planned.task.project_id,
    )
    return plan, step


def test_lost_worker_ack_reconciles_through_real_distributed_worker_without_redispatch() -> None:
    async def scenario() -> None:
        worker_lifecycle = FakeLifecycleBackend()
        runtime, _, local_worker = _distributed_runtime(worker_lifecycle)
        runtime.attach_worker(LostAcknowledgementWorker(local_worker))
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=DistributedLifecycleBackend(runtime),
            repository=InMemoryKernelRepository(),
        )
        plan, step = await _canonical_single_step(kernel, key="lost-ack")
        coordinator = DurablePlanStepCoordinator(
            repository=InMemoryCoordinatorRepository(),
            kernel=kernel,
            coordinator_id="issue-384-distributed-coordinator",
        )

        with pytest.raises(RuntimeError, match="acknowledgement loss"):
            await coordinator.register_plan(plan, (step,))

        before = coordinator.projection(plan.id).steps[0]
        assert before.status is StepStatus.RUNNING
        assert before.phase is CoordinationPhase.ATTEMPT_ACTIVE
        assert before.latest_run_id is not None
        run_id = before.latest_run_id
        assert (await kernel.get_run(plan.task_id, run_id)).status is RunStatus.STARTING
        assert len(worker_lifecycle.start_calls) == 1
        assert len(runtime.records()) == 1
        assert runtime.records()[0].state is DispatchState.LOST

        reconciled = await coordinator.reconcile_plan(plan.id)
        after = reconciled.steps[0]
        assert after.step_id == step.id
        assert after.latest_run_id == run_id
        assert after.current_attempt == 1
        assert after.status is StepStatus.RUNNING
        assert after.phase is CoordinationPhase.ATTEMPT_ACTIVE
        assert (await kernel.get_run(plan.task_id, run_id)).status is RunStatus.RUNNING
        assert (await kernel.get_task(plan.task_id)).status is TaskStatus.RUNNING
        assert runtime.records()[0].state is DispatchState.RUNNING
        assert len(worker_lifecycle.start_calls) == 1

        await coordinator.reconcile_plan(plan.id)
        assert coordinator.projection(plan.id).steps[0].latest_run_id == run_id
        assert len(worker_lifecycle.start_calls) == 1
        assert len(runtime.records()) == 1

    asyncio.run(scenario())


def test_plan_cancellation_reaches_worker_and_late_worker_success_cannot_revive_state() -> None:
    async def scenario() -> None:
        worker_lifecycle = FakeLifecycleBackend()
        runtime, _, local_worker = _distributed_runtime(worker_lifecycle)
        runtime.attach_worker(local_worker)
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=DistributedLifecycleBackend(runtime),
            repository=InMemoryKernelRepository(),
        )
        plan, step = await _canonical_single_step(kernel, key="cancel")
        coordinator = DurablePlanStepCoordinator(
            repository=InMemoryCoordinatorRepository(),
            kernel=kernel,
            coordinator_id="issue-384-cancellation-coordinator",
        )

        running = await coordinator.register_plan(plan, (step,))
        run_id = running.steps[0].latest_run_id
        assert run_id is not None
        assert running.steps[0].status is StepStatus.RUNNING
        assert len(runtime.records()) == 1
        assert len(worker_lifecycle.start_calls) == 1

        cancelled = await coordinator.cancel_plan(plan.id, idempotency_key="cancel-plan")
        assert cancelled.steps[0].step_id == step.id
        assert cancelled.steps[0].latest_run_id == run_id
        assert cancelled.steps[0].status is StepStatus.CANCELLED
        assert cancelled.steps[0].phase is CoordinationPhase.TERMINAL
        assert (await kernel.get_run(plan.task_id, run_id)).status is RunStatus.CANCELLED
        assert (await kernel.get_task(plan.task_id)).status is TaskStatus.CANCELLED
        assert len(worker_lifecycle.cancel_calls) == 1
        assert runtime.records()[0].state is DispatchState.TERMINAL
        assert runtime.records()[0].snapshot is not None
        assert runtime.records()[0].snapshot.status is ExecutionStatus.CANCELLED

        # Simulate an executor that reports a stale success after cancellation. The distributed
        # Worker Job is already terminal and canonical Task/Run/Step truth must not be revived.
        worker_lifecycle.complete(run_id, status=ExecutionStatus.SUCCEEDED)
        await runtime.reconcile()
        await coordinator.reconcile_plan(plan.id)

        final = coordinator.projection(plan.id).steps[0]
        assert final.step_id == step.id
        assert final.latest_run_id == run_id
        assert final.status is StepStatus.CANCELLED
        assert final.phase is CoordinationPhase.TERMINAL
        assert (await kernel.get_run(plan.task_id, run_id)).status is RunStatus.CANCELLED
        assert (await kernel.get_task(plan.task_id)).status is TaskStatus.CANCELLED
        assert runtime.records()[0].state is DispatchState.TERMINAL
        assert runtime.records()[0].snapshot is not None
        assert runtime.records()[0].snapshot.status is ExecutionStatus.CANCELLED
        assert len(worker_lifecycle.start_calls) == 1

    asyncio.run(scenario())
