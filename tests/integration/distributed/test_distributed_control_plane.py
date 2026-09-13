from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationProvider,
    ExecutionRequest,
    HealthStatus,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts.types import AuthorizationRequest
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
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

NOW = datetime(2026, 9, 3, 21, 0, tzinfo=UTC)


class DenyAuthorizationProvider(AuthorizationProvider):
    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="deny-distributed-control-plane",
            provider_type="authorization",
            health=HealthStatus.HEALTHY,
        )

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        del request
        return AuthorizationDecision(False, reason="distributed access denied by #15")


def _headers(idempotency_key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Request-Id": "request-issue-14",
        "X-Correlation-Id": "correlation-issue-14",
        "X-Principal-Ref": "user:test",
        "X-Owner-Type": "user",
        "X-Owner-Id": "test-owner",
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _node_and_worker() -> tuple[NodeRecord, WorkerRecord]:
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="control-plane-node",
        resources=ResourceSnapshot(
            cpu_cores_total=8.0,
            cpu_cores_available=8.0,
            ram_total_bytes=16_000,
            ram_available_bytes=16_000,
            storage_total_bytes=100_000,
            storage_available_bytes=100_000,
        ),
        labels=("local", "trusted"),
        supported_runtimes=("python",),
        trust_level="trusted",
    )
    worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        worker_type="reference",
        supported_executors=("reference",),
        supported_runtimes=("python",),
        concurrency_limit=2,
    )
    return node, worker


def _stack(
    authorization: AuthorizationProvider | None = None,
) -> tuple[ControlPlaneHTTP, DistributedRuntime, FakeLifecycleBackend, NodeRecord, WorkerRecord]:
    repository = InMemoryKernelRepository()
    lifecycle = FakeLifecycleBackend()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=lifecycle,
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=authorization,
    )
    runtime = DistributedRuntime(DistributedRegistry())
    node, worker = _node_and_worker()
    runtime.register(RegistrationRequest(node=node, workers=(worker,)), now=NOW)
    runtime.attach_worker(LocalWorker(worker.worker_id, lifecycle))
    register_distributed_control_plane(control_plane, runtime)
    return ControlPlaneHTTP(control_plane), runtime, lifecycle, node, worker


def _job() -> WorkerJobRequest:
    task_id = new_id("task")
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(correlation_id="correlation-worker-job"),
            input={"payload": "safe"},
        ),
        requirements=JobRequirements(
            executor_type="reference",
            cpu_cores_min=1.0,
            ram_min_bytes=512,
            runtime="python",
        ),
        workspace_ref="workspace:portable",
        snapshot_ref="snapshot:portable",
        artifact_refs=("artifact:input",),
        secret_refs=("secret:must-not-be-projected",),
        actor_ref="user:test",
        idempotency_key="worker-job-control-plane",
    )


def test_distributed_control_plane_exposes_runtime_state_and_admin_commands() -> None:
    async def scenario() -> None:
        http, runtime, lifecycle, node, worker = _stack()

        manifest = await http.handle(HTTPRequest(method="GET", path="/api/v1", headers=_headers()))
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        resources = manifest.body["resources"]
        commands = manifest.body["commands"]
        assert isinstance(resources, list)
        assert isinstance(commands, list)
        assert {"nodes", "workers", "worker-jobs"}.issubset(set(resources))
        assert {"node.drain", "worker.drain"}.issubset(set(commands))

        node_response = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/nodes/{node.node_id}",
                headers=_headers(),
            )
        )
        assert node_response.status == 200
        assert isinstance(node_response.body, dict)
        assert node_response.body["id"] == node.node_id
        assert node_response.body["worker_refs"] == [worker.worker_id]
        assert node_response.body["trust_level"] == "trusted"

        drained = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/node.drain",
                headers=_headers("node-drain-1"),
                body={"resource_ref": node.node_id},
            )
        )
        assert drained.status == 200
        assert isinstance(drained.body, dict)
        assert drained.body["draining"] is True
        assert runtime.registry.get_node(node.node_id).draining is True

        worker_response = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/workers/{worker.worker_id}",
                headers=_headers(),
            )
        )
        assert worker_response.status == 200
        assert isinstance(worker_response.body, dict)
        assert worker_response.body["node_id"] == node.node_id
        assert worker_response.body["protocol_version"] == worker.protocol_version

        undrained = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/node.undrain",
                headers=_headers("node-undrain-1"),
                body={"resource_ref": node.node_id},
            )
        )
        assert undrained.status == 200

        job = _job()
        await runtime.dispatch(job, now=NOW)
        assert len(lifecycle.start_calls) == 1
        job_response = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/worker-jobs/{job.worker_job_id}",
                headers=_headers(),
            )
        )
        assert job_response.status == 200
        assert isinstance(job_response.body, dict)
        assert job_response.body["run_id"] == job.execution.run_id
        assert job_response.body["workspace_ref"] == "workspace:portable"
        assert job_response.body["artifact_refs"] == ["artifact:input"]
        assert "secret_refs" not in job_response.body
        assert "secret:must-not-be-projected" not in str(job_response.body)

    asyncio.run(scenario())


def test_distributed_control_plane_mutations_require_idempotency_key() -> None:
    async def scenario() -> None:
        http, runtime, _, node, _ = _stack()
        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/node.drain",
                headers=_headers(),
                body={"resource_ref": node.node_id},
            )
        )
        assert response.status == 400
        assert runtime.registry.get_node(node.node_id).draining is False

    asyncio.run(scenario())


def test_distributed_control_plane_routes_reads_and_admin_commands_through_authorization() -> None:
    async def scenario() -> None:
        http, runtime, _, node, _ = _stack(DenyAuthorizationProvider())

        read_response = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/nodes/{node.node_id}",
                headers=_headers(),
            )
        )
        assert read_response.status == 403

        command_response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/node.drain",
                headers=_headers("denied-node-drain"),
                body={"resource_ref": node.node_id},
            )
        )
        assert command_response.status == 403
        assert runtime.registry.get_node(node.node_id).draining is False

    asyncio.run(scenario())
