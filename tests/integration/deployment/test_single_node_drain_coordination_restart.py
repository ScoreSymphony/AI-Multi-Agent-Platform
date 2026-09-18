from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    StepRetryPolicy,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.startup_recovery import reconcile_single_node_startup
from ai_multi_agent_platform.distributed import (
    DispatchState,
    DistributedRegistry,
    DistributedRuntime,
    LocalWorker,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import Plan, RunStatus, Step, StepStatus, new_id
from ai_multi_agent_platform.testing import FakeLifecycleBackend


async def _planned_single_step(deployment, *, key: str, owner_id: str) -> tuple[Plan, Step]:
    project_id = new_id("project")
    created = await deployment.kernel.create_task(
        idempotency_key=f"{key}:create",
        title=f"{key} task",
        objective=f"{key} objective",
        owner_type="user",
        owner_id=owner_id,
        project_id=project_id,
    )
    await deployment.kernel.ready_task(
        idempotency_key=f"{key}:ready",
        task_id=created.task_id,
    )
    planned = await deployment.kernel.plan_task(
        idempotency_key=f"{key}:plan",
        task_id=created.task_id,
    )
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


def test_progressing_plan_step_keeps_exact_identity_across_forced_drain_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        root = tmp_path / "progressing-step"
        first = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False)
        )
        admin = first.bootstrap_admin("drain-plan", "correct horse battery staple")
        plan, step = await _planned_single_step(
            first,
            key="drain-progress",
            owner_id=admin.user_id,
        )
        projection = await first.coordination.register_plan(plan, (step,))
        before = projection.steps[0]
        assert before.status is StepStatus.RUNNING
        assert before.phase is CoordinationPhase.ATTEMPT_ACTIVE
        assert before.latest_run_id is not None
        run_id = before.latest_run_id

        await first.drain.begin(reason="progressing_step_shutdown")
        await first.drain.mark_forced("test_forced_shutdown", timed_out=True)
        await first.drain.mark_completed()

        restarted = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False)
        )
        persisted = restarted.coordination_repository.get_plan(plan.id)
        restored_step = persisted.step(step.id)
        restored_record = restarted.coordination_repository.get_step_record(step.id)
        assert persisted.plan.id == plan.id
        assert restored_step.id == step.id
        assert restored_record.latest_run_id == run_id
        assert restored_record.current_attempt == 1

        recovery = await reconcile_single_node_startup(
            data_dir=root,
            kernel=restarted.kernel,
            coordinator=restarted.coordination,
            distributed_runtime=restarted.distributed_runtime,
            extensions=restarted.startup_recovery_extensions,
            reviewer_reconciler=restarted.reviewer_recovery,
        )
        after = restarted.coordination_repository.get_step_record(step.id)
        assert after.latest_run_id == run_id
        assert after.current_attempt == 1
        assert recovery.unresolved_run_ids == (run_id,)

    asyncio.run(scenario())


def test_retry_backoff_deadline_survives_drain_restart_without_early_retry(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        root = tmp_path / "retry-backoff"
        first = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False)
        )
        admin = first.bootstrap_admin("drain-retry", "correct horse battery staple")
        plan, step = await _planned_single_step(
            first,
            key="drain-retry",
            owner_id=admin.user_id,
        )
        policy = StepRetryPolicy(
            max_attempts=2,
            initial_delay_seconds=600,
            max_delay_seconds=600,
            retryable_categories=("transient",),
        )
        projection = await first.coordination.register_plan(
            plan,
            (step,),
            retry_policies={step.id: policy},
        )
        first_run = projection.steps[0].latest_run_id
        assert first_run is not None
        await first.kernel.record_run_outcome(
            idempotency_key="drain-retry:failed",
            task_id=plan.task_id,
            run_id=first_run,
            status=RunStatus.FAILED,
        )
        observed_at = datetime.now(UTC)
        projection = await first.coordination.observe_run(
            task_id=plan.task_id,
            run_id=first_run,
            failure_category="transient",
            now=observed_at,
        )
        scheduled = projection.steps[0]
        assert scheduled.phase is CoordinationPhase.RETRY_SCHEDULED
        assert scheduled.retry_due_at is not None
        retry_due_at = scheduled.retry_due_at

        await first.drain.begin(reason="retry_backoff_shutdown")

        restarted = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False)
        )
        restored = restarted.coordination_repository.get_step_record(step.id)
        assert restored.phase is CoordinationPhase.RETRY_SCHEDULED
        assert restored.retry_due_at == retry_due_at
        assert restored.latest_run_id == first_run
        assert restored.current_attempt == 1

        await restarted.coordination.process_due(now=observed_at)
        unchanged = restarted.coordination_repository.get_step_record(step.id)
        assert unchanged.latest_run_id == first_run
        assert unchanged.current_attempt == 1
        assert unchanged.retry_due_at == retry_due_at

    asyncio.run(scenario())


def _distributed_runtime(
    lifecycle: FakeLifecycleBackend,
) -> tuple[DistributedRuntime, LocalWorker]:
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="drain-worker-node",
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
    runtime.attach_worker(local_worker)
    return runtime, local_worker


def test_remote_worker_execution_is_not_cancelled_or_redispatched_by_drain(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        root = tmp_path / "remote-worker"
        lifecycle = FakeLifecycleBackend()
        runtime, _worker = _distributed_runtime(lifecycle)
        first = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False),
            distributed_runtime=runtime,
            enable_distributed_execution=True,
        )
        admin = first.bootstrap_admin("drain-worker", "correct horse battery staple")
        plan, step = await _planned_single_step(
            first,
            key="drain-worker",
            owner_id=admin.user_id,
        )
        projection = await first.coordination.register_plan(plan, (step,))
        run_id = projection.steps[0].latest_run_id
        assert run_id is not None
        assert len(runtime.records()) == 1
        assert len(lifecycle.start_calls) == 1
        assert len(lifecycle.cancel_calls) == 0

        await first.drain.begin(reason="remote_worker_shutdown")
        assert len(lifecycle.cancel_calls) == 0
        assert runtime.records()[0].state is DispatchState.RUNNING

        restarted = build_single_node_deployment(
            SingleNodeConfig(data_dir=root, secure_cookie=False),
            distributed_runtime=runtime,
            enable_distributed_execution=True,
        )
        recovery = await reconcile_single_node_startup(
            data_dir=root,
            kernel=restarted.kernel,
            coordinator=restarted.coordination,
            distributed_runtime=restarted.distributed_runtime,
            extensions=restarted.startup_recovery_extensions,
            reviewer_reconciler=restarted.reviewer_recovery,
        )

        assert len(runtime.records()) == 1
        assert len(lifecycle.start_calls) == 1
        assert len(lifecycle.cancel_calls) == 0
        assert restarted.coordination_repository.get_step_record(step.id).latest_run_id == run_id
        assert recovery.unresolved_run_ids == ()

    asyncio.run(scenario())
