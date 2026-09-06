"""Distributed Worker and remote Workspace scale evidence for issue #440."""

from __future__ import annotations

import asyncio
import time
import tracemalloc
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.adapters.single_node_app import build_default_single_node_deployment
from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, SingleNodeDeployment
from ai_multi_agent_platform.deployment.distributed_control_plane import (
    DeploymentWorkerProtocolService,
    platform_workspace_context,
)
from ai_multi_agent_platform.deployment.distributed_worker import (
    DistributedWorkerProcess,
    DistributedWorkerProcessConfig,
)
from ai_multi_agent_platform.distributed import (
    DispatchRecord,
    DispatchState,
    DistributedRegistry,
    DistributedRuntime,
    Heartbeat,
    NodeRecord,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerHeartbeatRequest,
    WorkerJobRequest,
    WorkerRecord,
    WorkerRequestCredentials,
)
from ai_multi_agent_platform.distributed.workspace_transport import DEFAULT_WORKSPACE_CHUNK_BYTES
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.messaging import InProcessMessageTransport
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    CredentialScope,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.workspaces import (
    WorkspaceAccessMode,
    WorkspaceFile,
    WorkspaceType,
)

from .models import LatencyDistribution, ResourceMetrics
from .single_node import (
    _directory_size,
    _environment_metadata,
    _open_file_descriptor_count,
    _peak_rss_bytes,
    _require_fresh_data_root,
)

DISTRIBUTED_SCALE_REPORT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class DistributedScaleSpec:
    """One bounded canonical distributed scheduling/materialization workload."""

    worker_count: int
    rounds: int
    payload_sizes_bytes: tuple[int, ...]
    chunk_bytes: int = DEFAULT_WORKSPACE_CHUNK_BYTES
    timeout_seconds: float = 30.0
    safety_max_operations: int = 2048
    safety_max_payload_bytes: int = 16 * 1024 * 1024
    safety_max_fixture_bytes: int = 64 * 1024 * 1024
    benchmark_id: str = "distributed.worker-workspace.scale"
    benchmark_version: str = "1.0"
    deployment_profile: str = "distributed-reference-in-process-transport"
    transport_profile: str = "in-process-message-transport"

    def __post_init__(self) -> None:
        if self.worker_count < 1:
            raise ValueError("worker_count must be at least 1")
        if self.rounds < 1:
            raise ValueError("rounds must be at least 1")
        if not self.payload_sizes_bytes:
            raise ValueError("payload_sizes_bytes must contain at least one size")
        if any(size < 1 for size in self.payload_sizes_bytes):
            raise ValueError("payload sizes must be positive")
        if len(set(self.payload_sizes_bytes)) != len(self.payload_sizes_bytes):
            raise ValueError("payload sizes must be unique")
        if tuple(sorted(self.payload_sizes_bytes)) != self.payload_sizes_bytes:
            raise ValueError("payload sizes must be strictly increasing")
        if self.chunk_bytes != DEFAULT_WORKSPACE_CHUNK_BYTES:
            raise ValueError(
                "distributed scale v1 records the fixed production Workspace chunk size"
            )
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.safety_max_operations < 1:
            raise ValueError("safety_max_operations must be at least 1")
        if self.safety_max_payload_bytes < 1:
            raise ValueError("safety_max_payload_bytes must be at least 1")
        if self.safety_max_fixture_bytes < 1:
            raise ValueError("safety_max_fixture_bytes must be at least 1")
        if self.operation_count > self.safety_max_operations:
            raise ValueError("distributed workload exceeds configured operation safety bound")
        if self.max_payload_bytes > self.safety_max_payload_bytes:
            raise ValueError("distributed payload exceeds configured payload safety bound")
        if self.fixture_bytes > self.safety_max_fixture_bytes:
            raise ValueError("distributed fixture bytes exceed configured fixture safety bound")

    @property
    def operation_count(self) -> int:
        return self.worker_count * self.rounds * len(self.payload_sizes_bytes)

    @property
    def max_payload_bytes(self) -> int:
        return max(self.payload_sizes_bytes)

    @property
    def fixture_bytes(self) -> int:
        return sum(self.payload_sizes_bytes)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "operation_count": self.operation_count,
            "max_payload_bytes": self.max_payload_bytes,
            "fixture_bytes": self.fixture_bytes,
            "expected_invariants": [
                "every configured Worker registers through the authenticated Worker protocol",
                "every Worker heartbeat refreshes the same canonical Node/Worker identity",
                "every Worker Job reaches a terminal canonical distributed-runtime record",
                "each full-width dispatch round uses every eligible Worker exactly once",
                "remote Workspace materialization is cleaned up after terminal result collection",
                "canonical Worker Job and Run identities remain unique",
            ],
            "captured_metrics": [
                "authenticated Worker registration latency p50/p95/p99",
                "authenticated heartbeat latency p50/p95/p99",
                "workspace-aware dispatch latency p50/p95/p99",
                "full-round reconciliation batch latency p50/p95/p99",
                "terminal result collection latency p50/p95/p99",
                "Worker Job throughput and placement distribution",
                "process CPU, memory, descriptor and storage evidence",
            ],
        }


@dataclass(frozen=True, slots=True)
class DistributedScaleCorrectnessSummary:
    expected_workers: int
    registered_workers: int
    heartbeat_workers: int
    expected_jobs: int
    terminal_jobs: int
    unique_worker_job_ids: int
    unique_run_ids: int
    workers_used: int
    balanced_rounds: int
    expected_balanced_rounds: int
    cleaned_materializations: int
    passed: bool


@dataclass(frozen=True, slots=True)
class DistributedScaleReport:
    schema_version: str
    benchmark: DistributedScaleSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: dict[str, Any]
    throughput_jobs_per_second: float
    registration_latency: LatencyDistribution
    heartbeat_latency: LatencyDistribution
    dispatch_latency: LatencyDistribution
    reconciliation_batch_latency: LatencyDistribution
    terminal_result_latency: LatencyDistribution
    placement_counts: dict[str, int]
    payload_operation_counts: dict[str, int]
    resources: ResourceMetrics
    correctness: DistributedScaleCorrectnessSummary
    node_ids: tuple[str, ...]
    worker_ids: tuple[str, ...]
    worker_job_ids: tuple[str, ...]
    run_ids: tuple[str, ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _json_compatible(asdict(self)))


@dataclass(frozen=True, slots=True)
class _WorkspaceFixture:
    workspace_id: str
    snapshot_id: str
    project_id: str
    payload_size_bytes: int


@dataclass(frozen=True, slots=True)
class _WorkerFixture:
    node: NodeRecord
    worker: WorkerRecord
    registration: RegistrationRequest
    token: str
    workspace_root: Path
    process: DistributedWorkerProcess


class DistributedWorkerWorkspaceScaleHarness:
    """Measure authenticated N-Worker dispatch with real remote Workspace adapters."""

    def __init__(self, data_dir: Path, *, platform_commit: str = "unknown") -> None:
        self._data_dir = data_dir
        self._platform_commit = platform_commit

    async def run(self, spec: DistributedScaleSpec) -> DistributedScaleReport:
        _require_fresh_data_root(self._data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        deployment = build_default_single_node_deployment(
            SingleNodeConfig(
                data_dir=self._data_dir / "control-plane",
                secure_cookie=False,
            )
        )
        runtime = DistributedRuntime(DistributedRegistry())
        transport = InProcessMessageTransport(provider_id="benchmark-distributed-scale")
        service = DeploymentWorkerProtocolService(
            runtime,
            authentication=deployment.authentication,
            authorization=deployment.authorization,
            transport=transport,
            workspaces=deployment.workspaces,
            files=deployment.files,
            context_resolver=platform_workspace_context,
        )

        worker_fixtures = self._build_workers(spec, deployment, transport)
        workspace_fixtures = await self._build_workspaces(spec, deployment)
        worker_tasks = [asyncio.create_task(item.process.run()) for item in worker_fixtures]
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        registration_samples: list[float] = []
        heartbeat_samples: list[float] = []
        dispatch_samples: list[float] = []
        reconciliation_batch_samples: list[float] = []
        terminal_result_samples: list[float] = []
        placement_counts: Counter[str] = Counter()
        payload_operation_counts: Counter[str] = Counter()
        worker_job_ids: list[str] = []
        run_ids: list[str] = []
        errors: list[str] = []
        balanced_rounds = 0

        storage_before = _directory_size(self._data_dir)
        cpu_before = time.process_time()
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        tracemalloc.start()
        try:
            async with asyncio.timeout(spec.timeout_seconds):
                for index, fixture in enumerate(worker_fixtures):
                    credentials = _credentials(
                        fixture.token,
                        nonce=f"benchmark-register-{index}",
                        correlation_id=f"benchmark-register-{index}",
                    )
                    sample_started = time.perf_counter()
                    receipt = await service.register(fixture.registration, credentials)
                    registration_samples.append(time.perf_counter() - sample_started)
                    if receipt.worker_ids != (fixture.worker.worker_id,):
                        errors.append(
                            f"registration receipt mismatch for {fixture.worker.worker_id}"
                        )

                for index, fixture in enumerate(worker_fixtures):
                    credentials = _credentials(
                        fixture.token,
                        nonce=f"benchmark-heartbeat-{index}",
                        correlation_id=f"benchmark-heartbeat-{index}",
                    )
                    sample_started = time.perf_counter()
                    receipt = await service.heartbeat(
                        WorkerHeartbeatRequest(
                            heartbeat=Heartbeat(
                                node_id=fixture.node.node_id,
                                sequence=1,
                                resources=fixture.node.resources,
                                workers=(fixture.worker,),
                            ),
                            service_identity_ref=fixture.worker.worker_id,
                        ),
                        credentials,
                    )
                    heartbeat_samples.append(time.perf_counter() - sample_started)
                    if receipt.worker_ids != (fixture.worker.worker_id,):
                        errors.append(f"heartbeat receipt mismatch for {fixture.worker.worker_id}")

                for workspace in workspace_fixtures:
                    for round_index in range(spec.rounds):
                        jobs = tuple(
                            _job(workspace, round_index=round_index, ordinal=ordinal)
                            for ordinal in range(spec.worker_count)
                        )
                        dispatched = await asyncio.gather(
                            *(self._dispatch_timed(runtime, job) for job in jobs)
                        )
                        round_workers = {record.worker_id for record, _ in dispatched}
                        if round_workers == {item.worker.worker_id for item in worker_fixtures}:
                            balanced_rounds += 1
                        for record, elapsed in dispatched:
                            dispatch_samples.append(elapsed)
                            placement_counts[record.worker_id] += 1
                            payload_operation_counts[str(workspace.payload_size_bytes)] += 1
                            worker_job_ids.append(record.job.worker_job_id)
                            run_ids.append(record.job.execution.run_id)

                        reconcile_started = time.perf_counter()
                        reconciled = await runtime.reconcile()
                        reconciliation_batch_samples.append(time.perf_counter() - reconcile_started)
                        current_ids = {job.worker_job_id for job in jobs}
                        current_records = tuple(
                            record
                            for record in reconciled
                            if record.job.worker_job_id in current_ids
                        )
                        for record in current_records:
                            if record.state is not DispatchState.TERMINAL:
                                errors.append(f"non-terminal worker job: {record.job.worker_job_id}")
                            result_started = time.perf_counter()
                            result = await runtime.result(record.job.worker_job_id)
                            terminal_result_samples.append(time.perf_counter() - result_started)
                            if result is None:
                                errors.append(
                                    f"terminal worker job has no result: {record.job.worker_job_id}"
                                )
        except TimeoutError:
            errors.append("distributed benchmark exceeded timeout")
        except Exception as exc:
            errors.append(f"distributed benchmark failed: {type(exc).__name__}: {exc}")
        finally:
            duration = max(0.0, time.perf_counter() - started)
            traced_current, traced_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            for fixture in worker_fixtures:
                fixture.process.stop()
            await asyncio.gather(*worker_tasks, return_exceptions=True)
            await transport.close(graceful=False)

        storage_after = _directory_size(self._data_dir)
        cleaned_materializations = sum(
            1
            for worker in worker_fixtures
            for workspace in workspace_fixtures
            if not (worker.workspace_root / workspace.workspace_id / workspace.snapshot_id).exists()
        )
        expected_cleaned = len(worker_fixtures) * len(workspace_fixtures)
        terminal_jobs = sum(
            1 for record in runtime.records() if record.state is DispatchState.TERMINAL
        )
        registered_workers = len(runtime.registry.list_workers())
        heartbeat_workers = sum(
            1
            for worker in runtime.registry.list_workers()
            if worker.last_heartbeat_at >= worker.registered_at
        )
        expected_balanced_rounds = spec.rounds * len(spec.payload_sizes_bytes)
        correctness = DistributedScaleCorrectnessSummary(
            expected_workers=spec.worker_count,
            registered_workers=registered_workers,
            heartbeat_workers=heartbeat_workers,
            expected_jobs=spec.operation_count,
            terminal_jobs=terminal_jobs,
            unique_worker_job_ids=len(set(worker_job_ids)),
            unique_run_ids=len(set(run_ids)),
            workers_used=len(placement_counts),
            balanced_rounds=balanced_rounds,
            expected_balanced_rounds=expected_balanced_rounds,
            cleaned_materializations=cleaned_materializations,
            passed=(
                not errors
                and registered_workers == spec.worker_count
                and heartbeat_workers == spec.worker_count
                and terminal_jobs == spec.operation_count
                and len(set(worker_job_ids)) == spec.operation_count
                and len(set(run_ids)) == spec.operation_count
                and len(placement_counts) == spec.worker_count
                and balanced_rounds == expected_balanced_rounds
                and cleaned_materializations == expected_cleaned
            ),
        )
        if not correctness.passed and not errors:
            errors.append("distributed correctness invariants failed")

        resources = ResourceMetrics(
            process_cpu_seconds=round(max(0.0, time.process_time() - cpu_before), 6),
            traced_memory_current_bytes=traced_current,
            traced_memory_peak_bytes=traced_peak,
            peak_rss_bytes=_peak_rss_bytes(),
            storage_bytes_before=storage_before,
            storage_bytes_after=storage_after,
            storage_growth_bytes=storage_after - storage_before,
            open_file_descriptors=_open_file_descriptor_count(),
        )
        throughput = spec.operation_count / duration if duration > 0 else 0.0
        return DistributedScaleReport(
            schema_version=DISTRIBUTED_SCALE_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at.isoformat(),
            duration_seconds=round(duration, 6),
            environment=_environment_metadata(),
            throughput_jobs_per_second=round(throughput, 6),
            registration_latency=LatencyDistribution.from_seconds(registration_samples),
            heartbeat_latency=LatencyDistribution.from_seconds(heartbeat_samples),
            dispatch_latency=LatencyDistribution.from_seconds(dispatch_samples),
            reconciliation_batch_latency=LatencyDistribution.from_seconds(
                reconciliation_batch_samples
            ),
            terminal_result_latency=LatencyDistribution.from_seconds(terminal_result_samples),
            placement_counts=dict(sorted(placement_counts.items())),
            payload_operation_counts=dict(
                sorted(payload_operation_counts.items(), key=lambda x: int(x[0]))
            ),
            resources=resources,
            correctness=correctness,
            node_ids=tuple(item.node.node_id for item in worker_fixtures),
            worker_ids=tuple(item.worker.worker_id for item in worker_fixtures),
            worker_job_ids=tuple(worker_job_ids),
            run_ids=tuple(run_ids),
            errors=tuple(errors),
        )

    def _build_workers(
        self,
        spec: DistributedScaleSpec,
        deployment: SingleNodeDeployment,
        transport: InProcessMessageTransport,
    ) -> tuple[_WorkerFixture, ...]:
        actions = frozenset(
            {
                AuthorizationAction.CREATE,
                AuthorizationAction.MODIFY,
                AuthorizationAction.DELETE,
            }
        )
        resource_types = frozenset({ResourceType.NODE, ResourceType.WORKER})
        fixtures: list[_WorkerFixture] = []
        worker_parent = (self._data_dir / "workers").resolve()
        for index in range(spec.worker_count):
            node = NodeRecord(
                node_id=new_id("node"),
                display_name=f"Benchmark Node {index}",
                resources=ResourceSnapshot(
                    cpu_cores_total=4.0,
                    cpu_cores_available=4.0,
                    ram_total_bytes=8 * 1024**3,
                    ram_available_bytes=8 * 1024**3,
                    storage_total_bytes=64 * 1024**3,
                    storage_available_bytes=64 * 1024**3,
                ),
                labels=("benchmark",),
            )
            worker = WorkerRecord(
                worker_id=new_id("worker"),
                node_id=node.node_id,
                concurrency_limit=1,
                locality_refs=(f"benchmark-node-{index}",),
            )
            registration = RegistrationRequest(
                node=node,
                workers=(worker,),
                service_identity_ref=worker.worker_id,
            )
            credential = deployment.authentication.create_worker_credential(
                worker.worker_id,
                scope=CredentialScope(actions=actions, resource_types=resource_types),
            )
            deployment.authorization.register(
                LocalPrincipalPolicy(
                    principal_ref=worker.worker_id,
                    actor_types=frozenset({ActorType.WORKER}),
                    allowed_actions=actions,
                    resource_types=resource_types,
                )
            )
            workspace_root = worker_parent / worker.worker_id
            process = DistributedWorkerProcess(
                DistributedWorkerProcessConfig(
                    registration=registration,
                    worker_id=worker.worker_id,
                    workspace_root=workspace_root,
                    reporting=False,
                ),
                protocol=None,
                transport=transport,
            )
            fixtures.append(
                _WorkerFixture(
                    node=node,
                    worker=worker,
                    registration=registration,
                    token=credential.secret,
                    workspace_root=workspace_root,
                    process=process,
                )
            )
        return tuple(fixtures)

    async def _build_workspaces(
        self,
        spec: DistributedScaleSpec,
        deployment: SingleNodeDeployment,
    ) -> tuple[_WorkspaceFixture, ...]:
        fixtures: list[_WorkspaceFixture] = []
        for payload_size in spec.payload_sizes_bytes:
            project_id = new_id("project")
            context = DataAccessContext(
                operation=OperationContext(
                    correlation_id=f"distributed-scale-fixture:{payload_size}",
                    project_id=project_id,
                ),
                actor_ref="service:platform",
            )
            record = await deployment.files.create_file(
                b"x" * payload_size,
                context,
                content_type="application/octet-stream",
            )
            workspace = await deployment.workspaces.create_workspace(
                project_id=project_id,
                owner_ref=OwnerRef(type="service", id="benchmark-distributed-scale"),
                workspace_type=WorkspaceType.REMOTE,
                context=context,
                access_mode=WorkspaceAccessMode.READ_WRITE,
                files=(
                    WorkspaceFile(
                        relative_path="payload.bin",
                        file_id=record.file_id,
                        sha256=record.sha256,
                    ),
                ),
            )
            if workspace.base_snapshot_id is None:
                raise RuntimeError("benchmark Workspace has no base snapshot")
            fixtures.append(
                _WorkspaceFixture(
                    workspace_id=workspace.id,
                    snapshot_id=workspace.base_snapshot_id,
                    project_id=project_id,
                    payload_size_bytes=payload_size,
                )
            )
        return tuple(fixtures)

    @staticmethod
    async def _dispatch_timed(
        runtime: DistributedRuntime,
        job: WorkerJobRequest,
    ) -> tuple[DispatchRecord, float]:
        started = time.perf_counter()
        record = await runtime.dispatch(job)
        return record, time.perf_counter() - started


def _credentials(token: str, *, nonce: str, correlation_id: str) -> WorkerRequestCredentials:
    return WorkerRequestCredentials(
        token=token,
        nonce=nonce,
        issued_at=datetime.now(UTC),
        tls_peer_ref="spiffe://benchmark/distributed-worker",
        request_id=nonce,
        correlation_id=correlation_id,
    )


def _job(
    workspace: _WorkspaceFixture,
    *,
    round_index: int,
    ordinal: int,
) -> WorkerJobRequest:
    task_id = new_id("task")
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(
                correlation_id=(
                    f"distributed-scale:{workspace.payload_size_bytes}:{round_index}:{ordinal}"
                ),
                project_id=workspace.project_id,
            ),
            input={"plan_ref": f"payload:{workspace.payload_size_bytes}"},
        ),
        workspace_ref=workspace.workspace_id,
        snapshot_ref=workspace.snapshot_id,
        idempotency_key=(
            f"distributed-scale:{workspace.payload_size_bytes}:{round_index}:{ordinal}"
        ),
    )


def _json_compatible(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    return value
