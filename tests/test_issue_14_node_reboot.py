from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts import (
    ExecutionRequest,
    OperationContext,
    OperationControl,
    RetryMode,
)
from ai_multi_agent_platform.distributed import (
    DispatchState,
    DistributedRegistry,
    DistributedRuntime,
    FailoverFenceReceipt,
    Heartbeat,
    JobRequirements,
    LocalWorker,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.testing import FakeLifecycleBackend

BASE_TIME = datetime(2026, 9, 4, 4, 0, tzinfo=UTC)


class _RebootFence:
    async def fence(
        self,
        *,
        worker_id: str,
        job: WorkerJobRequest,
    ) -> FailoverFenceReceipt:
        return FailoverFenceReceipt(
            worker_job_id=job.worker_job_id,
            worker_id=worker_id,
            fence_ref=f"node-reboot:{worker_id}",
            fenced_at=BASE_TIME + timedelta(seconds=33),
        )


def _node(name: str) -> NodeRecord:
    return NodeRecord(
        node_id=new_id("node"),
        display_name=name,
        resources=ResourceSnapshot(
            cpu_cores_total=8,
            cpu_cores_available=8,
            ram_total_bytes=16_000,
            ram_available_bytes=16_000,
            storage_total_bytes=100_000,
            storage_available_bytes=100_000,
        ),
        supported_runtimes=("python",),
    )


def _worker(node: NodeRecord) -> WorkerRecord:
    return WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        supported_executors=("reference",),
        supported_runtimes=("python",),
    )


def _job(worker_id: str) -> WorkerJobRequest:
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=new_id("task"),
            context=OperationContext(
                correlation_id="corr:issue-14-node-reboot",
                control=OperationControl(
                    idempotency_key="run:issue-14-node-reboot",
                    retry_mode=RetryMode.IDEMPOTENT,
                ),
            ),
        ),
        requirements=JobRequirements(
            executor_type="reference",
            runtime="python",
            preferred_worker_ids=(worker_id,),
        ),
        workspace_ref="workspace:node-reboot",
        snapshot_ref="snapshot:node-reboot",
        artifact_refs=("artifact:node-reboot",),
    )


def test_node_reboot_reregistration_does_not_assume_old_execution_survived() -> None:
    async def scenario() -> None:
        registry = DistributedRegistry(heartbeat_timeout=timedelta(seconds=30))
        node_a = _node("rebooting-node")
        node_b = _node("replacement-node")
        worker_a = _worker(node_a)
        worker_b = _worker(node_b)
        lifecycle_before_reboot = FakeLifecycleBackend()
        lifecycle_after_reboot = FakeLifecycleBackend()
        replacement_lifecycle = FakeLifecycleBackend()
        runtime = DistributedRuntime(registry, ownership_fencer=_RebootFence())
        runtime.register(RegistrationRequest(node=node_a, workers=(worker_a,)), now=BASE_TIME)
        runtime.register(RegistrationRequest(node=node_b, workers=(worker_b,)), now=BASE_TIME)
        runtime.attach_worker(LocalWorker(worker_a.worker_id, lifecycle_before_reboot))
        runtime.attach_worker(LocalWorker(worker_b.worker_id, replacement_lifecycle))
        job = _job(worker_a.worker_id)

        original = await runtime.dispatch(job, now=BASE_TIME)
        assert original.worker_id == worker_a.worker_id
        assert len(lifecycle_before_reboot.start_calls) == 1

        # Keep B live while A disappears long enough to model a host reboot.
        runtime.heartbeat(
            Heartbeat(
                node_id=node_b.node_id,
                sequence=1,
                observed_at=BASE_TIME + timedelta(seconds=20),
                workers=(worker_b,),
            )
        )
        runtime.detach_worker(worker_a.worker_id)
        lost = await runtime.reconcile(now=BASE_TIME + timedelta(seconds=31))
        current = next(item for item in lost if item.job.worker_job_id == job.worker_job_id)
        assert current.state is DispatchState.LOST

        # The rebooted host returns under the same canonical Node/Worker IDs, but its new
        # process has no in-memory ownership of the pre-reboot Worker Job.
        runtime.register(
            RegistrationRequest(node=node_a, workers=(worker_a,)),
            now=BASE_TIME + timedelta(seconds=32),
        )
        runtime.attach_worker(LocalWorker(worker_a.worker_id, lifecycle_after_reboot))
        reconciled = await runtime.reconcile(now=BASE_TIME + timedelta(seconds=32))
        after_reboot = next(
            item for item in reconciled if item.job.worker_job_id == job.worker_job_id
        )
        assert after_reboot.state is DispatchState.LOST
        assert lifecycle_after_reboot.start_calls == []

        replacement = await runtime.failover(
            job.worker_job_id,
            now=BASE_TIME + timedelta(seconds=33),
        )
        assert replacement.worker_id == worker_b.worker_id
        assert replacement.job.worker_job_id == job.worker_job_id
        assert replacement.job.execution == job.execution
        assert replacement.job.dispatch_attempt == 2
        assert len(lifecycle_before_reboot.start_calls) == 1
        assert lifecycle_after_reboot.start_calls == []
        assert len(replacement_lifecycle.start_calls) == 1

    asyncio.run(scenario())
