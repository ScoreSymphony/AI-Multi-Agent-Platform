from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ExecutionRequest, ExecutionSnapshot, OperationContext
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    JobRequirements,
    JobResultStatus,
    LocalWorker,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerJobResult,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.security.authorization import (
    ActorType,
    AuthorizationAction,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend


def _node_and_worker() -> tuple[NodeRecord, WorkerRecord]:
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="issue-14-final-hardening-node",
        resources=ResourceSnapshot(
            cpu_cores_total=4.0,
            cpu_cores_available=4.0,
            ram_total_bytes=8_000,
            ram_available_bytes=8_000,
            storage_total_bytes=100_000,
            storage_available_bytes=100_000,
        ),
        capability_refs=("worker.execute",),
    )
    worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        capability_refs=("worker.execute",),
        concurrency_limit=2,
    )
    return node, worker


def _job(
    *,
    actor_ref: str | None,
    owner_type: str | None,
    owner_id: str | None,
) -> WorkerJobRequest:
    return WorkerJobRequest(
        worker_job_id=new_id("worker_job"),
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=new_id("task"),
            context=OperationContext(
                correlation_id="issue-14-final-contract-hardening",
                owner_type=owner_type,
                owner_id=owner_id,
                project_id=new_id("project"),
            ),
        ),
        requirements=JobRequirements(capability_refs=("worker.execute",)),
        actor_ref=actor_ref,
    )


def _authorization(principal_ref: str, actor_type: ActorType) -> LocalAuthorizationProvider:
    return LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=principal_ref,
                actor_types=frozenset({actor_type}),
                allowed_actions=frozenset({AuthorizationAction.EXECUTE}),
                resource_types=frozenset({ResourceType.WORKER}),
            ),
        )
    )


def test_user_owned_dispatch_maps_owner_fallback_to_human_actor_type() -> None:
    node, worker = _node_and_worker()
    lifecycle = FakeLifecycleBackend()
    runtime = DistributedRuntime(
        DistributedRegistry(),
        authorization=_authorization("alice", ActorType.HUMAN),
    )
    runtime.register(RegistrationRequest(node=node, workers=(worker,)))
    runtime.attach_worker(LocalWorker(worker.worker_id, lifecycle))
    job = _job(actor_ref=None, owner_type="user", owner_id="alice")

    async def scenario() -> None:
        record = await runtime.dispatch_to_worker(job, worker.worker_id)
        assert record.worker_id == worker.worker_id
        assert len(lifecycle.start_calls) == 1

    asyncio.run(scenario())


def test_explicit_actor_ref_takes_precedence_over_resource_owner_type() -> None:
    node, worker = _node_and_worker()
    lifecycle = FakeLifecycleBackend()
    runtime = DistributedRuntime(
        DistributedRegistry(),
        authorization=_authorization("agent:planner", ActorType.AGENT),
    )
    runtime.register(RegistrationRequest(node=node, workers=(worker,)))
    runtime.attach_worker(LocalWorker(worker.worker_id, lifecycle))
    job = _job(
        actor_ref="agent:planner",
        owner_type="user",
        owner_id="alice",
    )

    async def scenario() -> None:
        record = await runtime.dispatch_to_worker(job, worker.worker_id)
        assert record.worker_id == worker.worker_id
        assert len(lifecycle.start_calls) == 1

    asyncio.run(scenario())


def test_dispatch_without_actor_or_owner_uses_platform_service_identity() -> None:
    node, worker = _node_and_worker()
    lifecycle = FakeLifecycleBackend()
    runtime = DistributedRuntime(
        DistributedRegistry(),
        authorization=_authorization("service:distributed-runtime", ActorType.SERVICE),
    )
    runtime.register(RegistrationRequest(node=node, workers=(worker,)))
    runtime.attach_worker(LocalWorker(worker.worker_id, lifecycle))
    job = _job(actor_ref=None, owner_type=None, owner_id=None)

    async def scenario() -> None:
        record = await runtime.dispatch_to_worker(job, worker.worker_id)
        assert record.worker_id == worker.worker_id
        assert len(lifecycle.start_calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("result_status", "execution_status"),
    (
        (JobResultStatus.SUCCEEDED, RunStatus.SUCCEEDED),
        (JobResultStatus.FAILED, RunStatus.FAILED),
        (JobResultStatus.CANCELLED, RunStatus.CANCELLED),
        (JobResultStatus.TIMED_OUT, RunStatus.TIMED_OUT),
    ),
)
def test_worker_job_result_accepts_matching_terminal_execution_status(
    result_status: JobResultStatus,
    execution_status: RunStatus,
) -> None:
    result = WorkerJobResult(
        worker_job_id=new_id("worker_job"),
        worker_id=new_id("worker"),
        status=result_status,
        execution=ExecutionSnapshot(
            run_id=new_id("run"),
            status=execution_status,
        ),
    )

    assert result.status is result_status
    assert result.execution is not None
    assert result.execution.status is execution_status


@pytest.mark.parametrize(
    ("result_status", "execution_status"),
    (
        (JobResultStatus.SUCCEEDED, RunStatus.FAILED),
        (JobResultStatus.FAILED, RunStatus.SUCCEEDED),
        (JobResultStatus.CANCELLED, RunStatus.TIMED_OUT),
        (JobResultStatus.TIMED_OUT, RunStatus.CANCELLED),
        (JobResultStatus.SUCCEEDED, RunStatus.RUNNING),
    ),
)
def test_worker_job_result_rejects_mismatched_or_non_terminal_execution_status(
    result_status: JobResultStatus,
    execution_status: RunStatus,
) -> None:
    with pytest.raises(
        ValueError,
        match="worker result status must match its terminal execution snapshot status",
    ):
        WorkerJobResult(
            worker_job_id=new_id("worker_job"),
            worker_id=new_id("worker"),
            status=result_status,
            execution=ExecutionSnapshot(
                run_id=new_id("run"),
                status=execution_status,
            ),
        )


def test_worker_job_result_without_execution_snapshot_remains_valid() -> None:
    result = WorkerJobResult(
        worker_job_id=new_id("worker_job"),
        worker_id=new_id("worker"),
        status=JobResultStatus.SUCCEEDED,
    )

    assert result.execution is None