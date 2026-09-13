from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts import ExecutionRequest
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    JobRequirements,
    LocalWorker,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerJobRequest,
    WorkerRecord,
    register_distributed_control_plane,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.search import LocalSearchProvider, SearchDocument
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)

NOW = datetime(2026, 9, 6, 0, 0, tzinfo=UTC)


class RecordingSearchProvider(LocalSearchProvider):
    def __init__(self) -> None:
        super().__init__()
        self.last_documents: tuple[SearchDocument, ...] = ()

    async def rebuild(
        self,
        documents: tuple[SearchDocument, ...],
        context: OperationContext,
    ) -> None:
        self.last_documents = documents
        await super().rebuild(documents, context)


def test_worker_jobs_exist_in_runtime_but_stay_out_of_global_search() -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=repository,
        )
        search_provider = RecordingSearchProvider()
        control_plane = ControlPlane(
            kernel=kernel,
            events=repository,
            authorization=FakeAuthorizationProvider(),
            search_provider=search_provider,
        )
        http = ControlPlaneHTTP(control_plane)

        runtime = DistributedRuntime(DistributedRegistry())
        node = NodeRecord(
            node_id=new_id("node"),
            display_name="Issue 288 worker-job boundary node",
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
            worker_type="reference",
            supported_executors=("reference",),
            supported_runtimes=("python",),
        )
        runtime.register(RegistrationRequest(node=node, workers=(worker,)), now=NOW)
        runtime.attach_worker(LocalWorker(worker.worker_id, lifecycle))
        register_distributed_control_plane(control_plane, runtime)

        job = WorkerJobRequest(
            execution=ExecutionRequest(
                run_id=new_id("run"),
                subject_type="task",
                subject_id=new_id("task"),
                context=OperationContext(correlation_id="issue-288-worker-job-boundary"),
                input={"payload": "scheduler-state-must-not-be-searchable"},
            ),
            requirements=JobRequirements(
                executor_type="reference",
                cpu_cores_min=1.0,
                ram_min_bytes=512,
                runtime="python",
            ),
            idempotency_key="issue-288-worker-job-boundary",
        )
        await runtime.dispatch(job, now=NOW)
        assert any(record.job.worker_job_id == job.worker_job_id for record in runtime.records())

        canonical = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/worker-jobs/{job.worker_job_id}",
            )
        )
        assert canonical.status == 200
        assert isinstance(canonical.body, dict)
        assert canonical.body["id"] == job.worker_job_id

        await control_plane.rebuild_search_index(correlation_id="issue-288-worker-job-rebuild")
        serialized_documents = repr(search_provider.last_documents)
        assert job.worker_job_id not in serialized_documents
        assert not any(
            document.resource_type in {"worker-job", "worker_job"}
            for document in search_provider.last_documents
        )

        search = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/search",
                query={"q": job.worker_job_id},
            )
        )
        assert search.status == 200
        assert isinstance(search.body, dict)
        assert search.body["total"] == 0
        assert search.body["items"] == []
        assert job.worker_job_id not in repr(search.body)

    asyncio.run(scenario())
