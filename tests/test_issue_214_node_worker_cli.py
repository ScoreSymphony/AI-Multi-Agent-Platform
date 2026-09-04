from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli
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

NOW = datetime(2026, 9, 4, 18, 0, tzinfo=UTC)


class InProcessTransport:
    def __init__(self, http: ControlPlaneHTTP) -> None:
        self.http = http

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        del timeout
        parsed = urlsplit(url)
        decoded: dict[str, Any] = {}
        if body:
            loaded = json.loads(body.decode("utf-8"))
            assert isinstance(loaded, dict)
            decoded = loaded
        response = asyncio.run(
            self.http.handle(
                HTTPRequest(
                    method=method,
                    path=parsed.path,
                    headers=headers,
                    query=dict(parse_qsl(parsed.query)),
                    body=decoded,
                )
            )
        )
        return RawResponse(
            status=response.status,
            body=json.dumps(response.body, default=str).encode("utf-8"),
            headers=response.headers,
        )


class DenyAuthorizationProvider(AuthorizationProvider):
    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="deny-issue-214-cli",
            provider_type="authorization",
            health=HealthStatus.HEALTHY,
        )

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        del request
        return AuthorizationDecision(False, reason="compute administration denied")


def _stack(
    authorization: AuthorizationProvider | None = None,
) -> tuple[InProcessTransport, DistributedRuntime, FakeLifecycleBackend, NodeRecord, WorkerRecord]:
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
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="issue-214-node",
        resources=ResourceSnapshot(
            cpu_cores_total=16.0,
            cpu_cores_available=12.0,
            ram_total_bytes=64_000,
            ram_available_bytes=48_000,
            storage_total_bytes=1_000_000,
            storage_available_bytes=750_000,
        ),
        labels=("local", "trusted"),
        supported_runtimes=("python", "container"),
        model_refs=("model:local",),
        capability_refs=("capability:code",),
        trust_level="trusted",
        network_available=True,
        locality_refs=("workspace:local",),
    )
    worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        worker_type="reference",
        supported_executors=("reference",),
        capability_refs=("capability:code",),
        supported_runtimes=("python",),
        model_refs=("model:local",),
        concurrency_limit=2,
        locality_refs=("workspace:local",),
    )
    runtime.register(RegistrationRequest(node=node, workers=(worker,)), now=NOW)
    runtime.attach_worker(LocalWorker(worker.worker_id, lifecycle))
    register_distributed_control_plane(control_plane, runtime)
    return InProcessTransport(ControlPlaneHTTP(control_plane)), runtime, lifecycle, node, worker


def _invoke(
    config: Path,
    transport: InProcessTransport,
    *arguments: str,
) -> tuple[int, dict[str, Any], str]:
    stdout = StringIO()
    stderr = StringIO()
    exit_code = run_cli(
        ["--config", str(config), "--json", *arguments],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    payload = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    assert isinstance(payload, dict)
    return exit_code, payload, stderr.getvalue()


def _job() -> WorkerJobRequest:
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=new_id("task"),
            context=OperationContext(correlation_id="issue-214-worker-job"),
            input={"payload": "safe"},
        ),
        requirements=JobRequirements(
            executor_type="reference",
            capability_refs=("capability:code",),
            cpu_cores_min=1.0,
            ram_min_bytes=512,
            model_ref="model:local",
            runtime="python",
        ),
        workspace_ref="workspace:portable",
        snapshot_ref="snapshot:portable",
        artifact_refs=("artifact:input",),
        secret_refs=("secret:must-not-leak",),
        actor_ref="user:operator",
        idempotency_key="issue-214-worker-job",
    )


def test_cli_inspects_nodes_workers_and_worker_jobs_through_control_plane(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    transport, runtime, lifecycle, node, worker = _stack()

    code, nodes, error = _invoke(config, transport, "node", "list")
    assert code == 0 and not error
    assert nodes["data"]["total"] == 1
    node_item = nodes["data"]["items"][0]
    assert node_item["id"] == node.node_id
    assert node_item["status"] == "online"
    assert node_item["resources"]["cpu_cores_total"] == 16.0
    assert node_item["resources"]["ram_available_bytes"] == 48_000
    assert node_item["capability_refs"] == ["capability:code"]
    assert node_item["model_refs"] == ["model:local"]
    assert node_item["supported_runtimes"] == ["python", "container"]

    code, shown_node, _ = _invoke(config, transport, "node", "show", node.node_id)
    assert code == 0
    assert shown_node["data"]["worker_refs"] == [worker.worker_id]

    code, workers, _ = _invoke(config, transport, "worker", "list")
    assert code == 0
    worker_item = workers["data"]["items"][0]
    assert worker_item["id"] == worker.worker_id
    assert worker_item["node_id"] == node.node_id
    assert worker_item["active_jobs"] == 0
    assert worker_item["capability_refs"] == ["capability:code"]

    job = _job()
    asyncio.run(runtime.dispatch(job, now=NOW))
    assert len(lifecycle.start_calls) == 1

    code, worker_job, _ = _invoke(
        config,
        transport,
        "worker-job",
        "show",
        job.worker_job_id,
    )
    assert code == 0
    assert worker_job["data"]["worker_id"] == worker.worker_id
    assert worker_job["data"]["run_id"] == job.execution.run_id
    assert worker_job["data"]["workspace_ref"] == "workspace:portable"
    assert worker_job["data"]["requirements"]["model_ref"] == "model:local"
    assert "secret_refs" not in worker_job["data"]
    assert "secret:must-not-leak" not in json.dumps(worker_job)


def test_cli_compute_admin_requires_confirmation_and_uses_canonical_commands(
    tmp_path: Path,
) -> None:
    config = tmp_path / "cli.json"
    transport, runtime, _, node, worker = _stack()

    code, _, error = _invoke(config, transport, "node", "drain", node.node_id)
    assert code == 2
    assert "requires confirmation" in error
    assert runtime.registry.get_node(node.node_id).draining is False

    code, drained, error = _invoke(
        config,
        transport,
        "--yes",
        "node",
        "drain",
        node.node_id,
        "--idempotency-key",
        "issue-214-node-drain",
    )
    assert code == 0 and not error
    assert drained["data"]["draining"] is True
    assert runtime.registry.get_node(node.node_id).draining is True

    assert _invoke(config, transport, "--yes", "node", "undrain", node.node_id)[0] == 0
    assert runtime.registry.get_node(node.node_id).draining is False

    assert _invoke(config, transport, "--yes", "node", "maintenance-enable", node.node_id)[0] == 0
    assert runtime.registry.get_node(node.node_id).maintenance is True
    assert _invoke(config, transport, "--yes", "node", "maintenance-disable", node.node_id)[0] == 0
    assert runtime.registry.get_node(node.node_id).maintenance is False

    assert _invoke(config, transport, "--yes", "worker", "drain", worker.worker_id)[0] == 0
    assert runtime.registry.get_worker(worker.worker_id).draining is True
    assert _invoke(config, transport, "--yes", "worker", "undrain", worker.worker_id)[0] == 0
    assert runtime.registry.get_worker(worker.worker_id).draining is False


def test_cli_compute_admin_obeys_authorization_without_mutating_runtime(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    transport, runtime, _, node, worker = _stack(DenyAuthorizationProvider())

    code, _, error = _invoke(config, transport, "node", "show", node.node_id)
    assert code == 3
    assert "authorization" in error or "denied" in error

    code, _, error = _invoke(
        config,
        transport,
        "--yes",
        "worker",
        "drain",
        worker.worker_id,
        "--idempotency-key",
        "issue-214-denied-worker-drain",
    )
    assert code == 3
    assert "authorization" in error or "denied" in error
    assert runtime.registry.get_worker(worker.worker_id).draining is False


def test_cli_doctor_reports_optional_compute_health_and_degradation(tmp_path: Path) -> None:
    config = tmp_path / "cli.json"
    transport, runtime, _, node, worker = _stack()

    code, healthy, error = _invoke(config, transport, "doctor")
    assert code == 0 and not error
    assert healthy["data"]["summary"] == "healthy"
    checks = healthy["data"]["checks"]
    assert any(
        item.get("name") == "node_health"
        and item.get("resource_id") == node.node_id
        and item.get("status") == "healthy"
        for item in checks
    )
    assert any(
        item.get("name") == "worker_health"
        and item.get("resource_id") == worker.worker_id
        and item.get("status") == "healthy"
        for item in checks
    )

    runtime.set_worker_draining(worker.worker_id, draining=True)
    code, degraded, error = _invoke(config, transport, "doctor")
    assert code == 1 and not error
    assert degraded["data"]["summary"] == "degraded"
    assert any(
        item.get("name") == "worker_health"
        and item.get("resource_id") == worker.worker_id
        and item.get("status") == "degraded"
        and item.get("draining") is True
        for item in degraded["data"]["checks"]
    )
