from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import (
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    OperationContext,
)
from ai_multi_agent_platform.contracts.authorization import AuthorizationRequest
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.distributed import (
    DISTRIBUTED_STATE_SCHEMA_VERSION,
    DispatchState,
    DistributedRegistry,
    DistributedRuntime,
    JobRequirements,
    JobResultStatus,
    JsonDistributedStateStore,
    LocalWorker,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerJobResourceService,
    WorkerJobResult,
    WorkerRecord,
)
from ai_multi_agent_platform.distributed.runtime import DispatchAuthorizationError
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.testing import FakeAuthorizationProvider, FakeLifecycleBackend


def _node_and_worker() -> tuple[NodeRecord, WorkerRecord]:
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="issue-14-security-result-node",
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


def _job() -> WorkerJobRequest:
    project_id = new_id("project")
    task_id = new_id("task")
    return WorkerJobRequest(
        worker_job_id=new_id("worker_job"),
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(
                correlation_id="issue-14-security-result-recovery",
                owner_type="service",
                owner_id="service:orchestrator",
                project_id=project_id,
            ),
        ),
        requirements=JobRequirements(capability_refs=("worker.execute",)),
        workspace_ref=new_id("workspace"),
        snapshot_ref=new_id("workspace_snapshot"),
        artifact_refs=("artifact:input",),
        secret_refs=("secret:must-not-leak",),
        actor_ref="service:orchestrator",
    )


def test_dispatch_authorization_denial_releases_reservation_before_worker_execution() -> None:
    node, worker = _node_and_worker()
    authorization = FakeAuthorizationProvider(allowed=False)
    lifecycle = FakeLifecycleBackend()
    runtime = DistributedRuntime(
        DistributedRegistry(),
        authorization=authorization,
    )
    runtime.register(RegistrationRequest(node=node, workers=(worker,)))
    runtime.attach_worker(LocalWorker(worker.worker_id, lifecycle))
    job = _job()

    async def scenario() -> None:
        with pytest.raises(DispatchAuthorizationError, match="fake-policy"):
            await runtime.dispatch_to_worker(job, worker.worker_id)

        assert lifecycle.start_calls == []
        assert runtime.registry.active_reservations() == ()
        assert runtime.records() == ()
        assert len(authorization.calls) == 1
        request = authorization.calls[0]
        assert isinstance(request, AuthorizationRequest)
        assert request.principal_ref == "service:orchestrator"
        assert request.action == "execute"
        assert request.resource_ref == worker.worker_id
        assert request.resource_type == "worker"
        assert request.node_id == node.node_id
        assert request.workspace_id == job.workspace_ref
        assert request.task_id == job.execution.subject_id
        assert request.run_id == job.execution.run_id
        assert request.capability_ref == "worker.execute"
        assert request.side_effect == "worker.dispatch"
        assert "secret:must-not-leak" not in repr(request)

    asyncio.run(scenario())


class _DurableResultWorker:
    def __init__(self, worker_id: str) -> None:
        self._worker_id = worker_id
        self.jobs: dict[str, WorkerJobRequest] = {}
        self.dispatch_calls = 0
        self.result_calls = 0
        self.drop_first_result = True
        self.output_artifact = new_id("artifact")
        self.evidence_ref = "evidence:issue-14-result-recovery"

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        self.dispatch_calls += 1
        self.jobs[job.worker_job_id] = job
        return ExecutionHandle(run_id=job.execution.run_id, backend_ref="result-recovery-worker")

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        job = self.jobs[worker_job_id]
        return ExecutionSnapshot(
            run_id=job.execution.run_id,
            status=RunStatus.SUCCEEDED,
            output={"result": "complete"},
        )

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        job = self.jobs[worker_job_id]
        return ExecutionSnapshot(
            run_id=job.execution.run_id,
            status=RunStatus.CANCELLED,
        )

    async def result(self, worker_job_id: str) -> WorkerJobResult | None:
        self.result_calls += 1
        if self.drop_first_result:
            self.drop_first_result = False
            raise RuntimeError("simulated lost completion response")
        snapshot = await self.get(worker_job_id)
        return WorkerJobResult(
            worker_job_id=worker_job_id,
            worker_id=self.worker_id,
            status=JobResultStatus.SUCCEEDED,
            execution=snapshot,
            artifact_refs=(self.output_artifact,),
            evidence_refs=(self.evidence_ref,),
        )


def test_terminal_result_is_recovered_after_restart_and_then_survives_without_worker(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "distributed-result-recovery.json"
    node, worker_record = _node_and_worker()
    request = RegistrationRequest(node=node, workers=(worker_record,))
    worker = _DurableResultWorker(worker_record.worker_id)
    job = _job()

    async def scenario() -> None:
        first_runtime = DistributedRuntime(
            DistributedRegistry(),
            state_store=JsonDistributedStateStore(state_path),
        )
        first_runtime.register(request)
        first_runtime.attach_worker(worker)
        await first_runtime.dispatch_to_worker(job, worker.worker_id)
        reconciled = await first_runtime.reconcile()
        assert reconciled[0].state is DispatchState.TERMINAL
        assert reconciled[0].result is None
        assert worker.dispatch_calls == 1

        with pytest.raises(RuntimeError, match="lost completion response"):
            await first_runtime.result(job.worker_job_id)
        assert first_runtime.get_record(job.worker_job_id).result is None
        assert worker.dispatch_calls == 1

        restored_runtime = DistributedRuntime(
            DistributedRegistry(),
            state_store=JsonDistributedStateStore(state_path),
        )
        assert restored_runtime.get_record(job.worker_job_id).result is None
        restored_runtime.register(request)
        restored_runtime.attach_worker(worker)

        recovered = await restored_runtime.result(job.worker_job_id)
        assert recovered is not None
        assert recovered.worker_job_id == job.worker_job_id
        assert recovered.worker_id == worker.worker_id
        assert recovered.artifact_refs == (worker.output_artifact,)
        assert recovered.evidence_refs == (worker.evidence_ref,)
        assert worker.dispatch_calls == 1
        assert worker.result_calls == 2

        final_runtime = DistributedRuntime(
            DistributedRegistry(),
            state_store=JsonDistributedStateStore(state_path),
        )
        durable = await final_runtime.result(job.worker_job_id)
        assert durable == recovered
        assert final_runtime.get_record(job.worker_job_id).result == recovered
        assert worker.dispatch_calls == 1
        assert worker.result_calls == 2

        projection = await WorkerJobResourceService(final_runtime).get_resource(
            RequestContext(
                request_id="request-issue-14-result-recovery",
                correlation_id="correlation-issue-14-result-recovery",
            ),
            job.worker_job_id,
        )
        assert projection["artifact_refs"] == ["artifact:input"]
        result_projection = projection["result"]
        assert isinstance(result_projection, dict)
        assert result_projection["status"] == "succeeded"
        assert result_projection["artifact_refs"] == [worker.output_artifact]
        assert result_projection["evidence_refs"] == [worker.evidence_ref]
        assert result_projection["execution_status"] == "succeeded"
        assert "secret_refs" not in projection
        assert "secret:must-not-leak" not in repr(projection)

    asyncio.run(scenario())


def test_schema_v1_state_without_worker_result_remains_restorable(tmp_path: Path) -> None:
    state_path = tmp_path / "distributed-v1-compat.json"
    node, worker_record = _node_and_worker()
    worker = _DurableResultWorker(worker_record.worker_id)
    job = _job()

    async def create_current_state() -> None:
        runtime = DistributedRuntime(
            DistributedRegistry(),
            state_store=JsonDistributedStateStore(state_path),
        )
        runtime.register(RegistrationRequest(node=node, workers=(worker_record,)))
        runtime.attach_worker(worker)
        await runtime.dispatch_to_worker(job, worker.worker_id)

    asyncio.run(create_current_state())

    document = json.loads(state_path.read_text(encoding="utf-8"))
    assert document["schema_version"] == DISTRIBUTED_STATE_SCHEMA_VERSION == "3"
    document["schema_version"] = "1"
    for record in document["dispatch_records"]:
        record.pop("result", None)
    state_path.write_text(json.dumps(document), encoding="utf-8")

    restored = DistributedRuntime(
        DistributedRegistry(),
        state_store=JsonDistributedStateStore(state_path),
    )
    record = restored.get_record(job.worker_job_id)
    assert record.job == job
    assert record.result is None
    assert record.worker_id == worker.worker_id
