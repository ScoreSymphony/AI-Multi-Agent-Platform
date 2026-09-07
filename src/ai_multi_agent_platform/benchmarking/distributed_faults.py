"""Distributed Worker loss/rejoin and remote Workspace failure evidence for issue #440."""

from __future__ import annotations

import asyncio
import time
import tracemalloc
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionRequest,
    OperationContext,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
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
    WorkerHeartbeatRequest,
    WorkerJobRequest,
    WorkerRequestCredentials,
    WorkerStatus,
)
from ai_multi_agent_platform.messaging import InProcessMessageTransport

from .distributed_scale import DistributedScaleSpec, DistributedWorkerWorkspaceScaleHarness
from .models import LatencyDistribution, ResourceMetrics
from .single_node import (
    _directory_size,
    _environment_metadata,
    _open_file_descriptor_count,
    _peak_rss_bytes,
    _require_fresh_data_root,
)

DISTRIBUTED_FAULT_REPORT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class DistributedFaultSpec:
    """One bounded reference fault-under-load workload for distributed execution."""

    worker_count: int = 3
    pre_fault_rounds: int = 1
    degraded_rounds: int = 1
    post_rejoin_rounds: int = 1
    payload_bytes: int = 1024
    heartbeat_timeout_seconds: float = 1.0
    reservation_ttl_seconds: float = 1.0
    timeout_seconds: float = 30.0
    safety_max_operations: int = 1024
    safety_max_payload_bytes: int = 16 * 1024 * 1024
    benchmark_id: str = "distributed.worker-workspace.faults"
    benchmark_version: str = "1.0"
    deployment_profile: str = "distributed-reference-in-process-transport"
    transport_profile: str = "in-process-message-transport"

    def __post_init__(self) -> None:
        if self.worker_count < 2:
            raise ValueError("worker_count must be at least 2 for loss/rejoin evidence")
        for value, label in (
            (self.pre_fault_rounds, "pre_fault_rounds"),
            (self.degraded_rounds, "degraded_rounds"),
            (self.post_rejoin_rounds, "post_rejoin_rounds"),
        ):
            if value < 1:
                raise ValueError(f"{label} must be at least 1")
        if self.payload_bytes < 1:
            raise ValueError("payload_bytes must be positive")
        if self.heartbeat_timeout_seconds <= 0:
            raise ValueError("heartbeat_timeout_seconds must be positive")
        if self.reservation_ttl_seconds <= 0:
            raise ValueError("reservation_ttl_seconds must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.safety_max_operations < 1:
            raise ValueError("safety_max_operations must be at least 1")
        if self.safety_max_payload_bytes < 1:
            raise ValueError("safety_max_payload_bytes must be at least 1")
        if self.total_attempts > self.safety_max_operations:
            raise ValueError("distributed fault workload exceeds configured operation safety bound")
        if self.payload_bytes > self.safety_max_payload_bytes:
            raise ValueError("distributed fault payload exceeds configured payload safety bound")

    @property
    def pre_fault_jobs(self) -> int:
        return self.worker_count * self.pre_fault_rounds

    @property
    def degraded_jobs(self) -> int:
        return (self.worker_count - 1) * self.degraded_rounds

    @property
    def post_rejoin_jobs(self) -> int:
        return self.worker_count * self.post_rejoin_rounds

    @property
    def expected_successful_jobs(self) -> int:
        return self.pre_fault_jobs + self.degraded_jobs + self.post_rejoin_jobs + 1

    @property
    def total_attempts(self) -> int:
        # One expected Workspace failure plus one recovery job are included.
        return self.expected_successful_jobs + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "pre_fault_jobs": self.pre_fault_jobs,
            "degraded_jobs": self.degraded_jobs,
            "post_rejoin_jobs": self.post_rejoin_jobs,
            "expected_successful_jobs": self.expected_successful_jobs,
            "total_attempts": self.total_attempts,
            "expected_invariants": [
                "only the stopped Worker expires while healthy Workers remain schedulable",
                "degraded load never dispatches to the offline Worker",
                "Worker rejoin preserves the canonical Node and Worker identities",
                "post-rejoin full-width load uses the rejoined Worker again",
                "Workspace transport outage surfaces retryable canonical UNAVAILABLE",
                "the failed Workspace dispatch never reaches Worker execution",
                "capacity recovers after the failed reservation expires",
                "the recovery Workspace job terminates and leaves no materialization behind",
            ],
            "captured_metrics": [
                "pre-fault dispatch latency p50/p95/p99",
                "Worker loss reconciliation latency",
                "degraded dispatch latency p50/p95/p99",
                "same-ID Worker re-registration latency",
                "post-rejoin dispatch latency p50/p95/p99",
                "Workspace failure detection latency",
                "Workspace recovery dispatch latency",
                "successful Worker Job throughput and process resource evidence",
            ],
        }


@dataclass(frozen=True, slots=True)
class DistributedFaultCorrectnessSummary:
    expected_workers: int
    stable_worker_ids: int
    lost_worker_offline: bool
    degraded_jobs: int
    degraded_terminal_jobs: int
    degraded_avoided_lost_worker: bool
    rejoined_worker_online: bool
    rejoin_preserved_identity: bool
    post_rejoin_jobs: int
    post_rejoin_terminal_jobs: int
    post_rejoin_used_rejoined_worker: bool
    workspace_failure_observed: bool
    workspace_failure_code: str | None
    workspace_failure_retryable: bool
    workspace_failure_record_lost: bool
    workspace_failure_reached_execution: bool
    workspace_recovery_terminal: bool
    workspace_cleanup_succeeded: bool
    expected_successful_jobs: int
    terminal_successful_jobs: int
    duplicate_worker_job_ids: int
    duplicate_run_ids: int
    passed: bool


@dataclass(frozen=True, slots=True)
class DistributedFaultReport:
    schema_version: str
    benchmark: DistributedFaultSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: dict[str, Any]
    throughput_successful_jobs_per_second: float
    pre_fault_dispatch_latency: LatencyDistribution
    worker_loss_reconciliation_latency: LatencyDistribution
    degraded_dispatch_latency: LatencyDistribution
    worker_rejoin_latency: LatencyDistribution
    post_rejoin_dispatch_latency: LatencyDistribution
    workspace_failure_latency: LatencyDistribution
    workspace_recovery_dispatch_latency: LatencyDistribution
    placement_counts: dict[str, int]
    resources: ResourceMetrics
    correctness: DistributedFaultCorrectnessSummary
    lost_worker_id: str
    worker_ids: tuple[str, ...]
    worker_job_ids: tuple[str, ...]
    run_ids: tuple[str, ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        document = asdict(self)
        document["benchmark"] = self.benchmark.to_dict()
        return cast(dict[str, Any], _json_compatible(document))


@dataclass(frozen=True, slots=True)
class _RoundResult:
    records: tuple[DispatchRecord, ...]
    dispatch_samples: tuple[float, ...]
    worker_job_ids: tuple[str, ...]
    run_ids: tuple[str, ...]


class DistributedWorkerWorkspaceFaultHarness(DistributedWorkerWorkspaceScaleHarness):
    """Exercise real distributed liveness and Workspace transport failure/recovery paths."""

    async def run(self, spec: DistributedFaultSpec) -> DistributedFaultReport:
        _require_fresh_data_root(self._data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        deployment = build_single_node_deployment(
            SingleNodeConfig(
                data_dir=self._data_dir / "control-plane",
                secure_cookie=False,
            )
        )
        registry = DistributedRegistry(
            heartbeat_timeout=timedelta(seconds=spec.heartbeat_timeout_seconds),
            reservation_ttl=timedelta(seconds=spec.reservation_ttl_seconds),
        )
        runtime = DistributedRuntime(registry)
        transport = InProcessMessageTransport(provider_id="benchmark-distributed-faults")
        service = DeploymentWorkerProtocolService(
            runtime,
            authentication=deployment.authentication,
            authorization=deployment.authorization,
            transport=transport,
            workspaces=deployment.workspaces,
            files=deployment.files,
            context_resolver=platform_workspace_context,
        )

        fixture_spec = DistributedScaleSpec(
            worker_count=spec.worker_count,
            rounds=1,
            payload_sizes_bytes=(spec.payload_bytes,),
            timeout_seconds=spec.timeout_seconds,
            safety_max_operations=max(spec.worker_count, 1),
            safety_max_payload_bytes=spec.safety_max_payload_bytes,
            safety_max_fixture_bytes=spec.safety_max_payload_bytes,
        )
        worker_fixtures = self._build_workers(fixture_spec, deployment, transport)
        workspace = (await self._build_workspaces(fixture_spec, deployment))[0]

        processes: dict[str, DistributedWorkerProcess] = {
            item.worker.worker_id: item.process for item in worker_fixtures
        }
        process_tasks: dict[str, asyncio.Task[None]] = {
            worker_id: asyncio.create_task(process.run())
            for worker_id, process in processes.items()
        }
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        pre_fault_samples: list[float] = []
        loss_samples: list[float] = []
        degraded_samples: list[float] = []
        rejoin_samples: list[float] = []
        post_rejoin_samples: list[float] = []
        workspace_failure_samples: list[float] = []
        workspace_recovery_samples: list[float] = []
        placement_counts: Counter[str] = Counter()
        worker_job_ids: list[str] = []
        run_ids: list[str] = []
        errors: list[str] = []

        base_time = datetime.now(UTC)
        sequences = {item.worker.worker_id: 0 for item in worker_fixtures}
        victim = worker_fixtures[0]
        victim_id = victim.worker.worker_id
        initial_worker_ids = tuple(item.worker.worker_id for item in worker_fixtures)

        degraded_terminal_jobs = 0
        degraded_workers: set[str] = set()
        post_rejoin_terminal_jobs = 0
        post_rejoin_workers: set[str] = set()
        lost_worker_offline = False
        rejoined_worker_online = False
        rejoin_preserved_identity = False
        workspace_failure_observed = False
        workspace_failure_code: str | None = None
        workspace_failure_retryable = False
        workspace_failure_record_lost = False
        workspace_failure_reached_execution = False
        workspace_recovery_terminal = False

        storage_before = _directory_size(self._data_dir)
        cpu_before = time.process_time()
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        tracemalloc.start()
        try:
            async with asyncio.timeout(spec.timeout_seconds):
                for index, fixture in enumerate(worker_fixtures):
                    await service.register(
                        fixture.registration,
                        _credentials(
                            fixture.token,
                            nonce=f"fault-register-{index}",
                            correlation_id=f"fault-register-{index}",
                        ),
                        now=base_time,
                    )
                    sequences[fixture.worker.worker_id] = 1
                    await service.heartbeat(
                        WorkerHeartbeatRequest(
                            heartbeat=Heartbeat(
                                node_id=fixture.node.node_id,
                                sequence=1,
                                resources=fixture.node.resources,
                                workers=(fixture.worker,),
                            ),
                            service_identity_ref=fixture.worker.worker_id,
                        ),
                        _credentials(
                            fixture.token,
                            nonce=f"fault-heartbeat-{index}",
                            correlation_id=f"fault-heartbeat-{index}",
                        ),
                        now=base_time,
                    )

                for round_index in range(spec.pre_fault_rounds):
                    result = await self._run_round(
                        runtime,
                        workspace,
                        phase="pre-fault",
                        round_index=round_index,
                        job_count=spec.worker_count,
                        now=base_time,
                    )
                    pre_fault_samples.extend(result.dispatch_samples)
                    self._record_round(result, placement_counts, worker_job_ids, run_ids)
                    if any(record.state is not DispatchState.TERMINAL for record in result.records):
                        errors.append("pre-fault round did not terminate every Worker Job")

                victim.process.stop()
                await process_tasks[victim_id]

                fault_time = base_time + timedelta(
                    seconds=spec.heartbeat_timeout_seconds + 0.1
                )
                for index, fixture in enumerate(worker_fixtures[1:], start=1):
                    worker_id = fixture.worker.worker_id
                    sequences[worker_id] += 1
                    await service.heartbeat(
                        WorkerHeartbeatRequest(
                            heartbeat=Heartbeat(
                                node_id=fixture.node.node_id,
                                sequence=sequences[worker_id],
                                resources=fixture.node.resources,
                                workers=(fixture.worker,),
                            ),
                            service_identity_ref=worker_id,
                        ),
                        _credentials(
                            fixture.token,
                            nonce=f"fault-healthy-heartbeat-{index}",
                            correlation_id=f"fault-healthy-heartbeat-{index}",
                        ),
                        now=fault_time,
                    )

                loss_started = time.perf_counter()
                await runtime.reconcile(now=fault_time)
                loss_samples.append(time.perf_counter() - loss_started)
                lost_worker_offline = (
                    runtime.registry.get_worker(victim_id).status is WorkerStatus.OFFLINE
                )
                if not lost_worker_offline:
                    errors.append("stopped Worker did not expire to OFFLINE")

                for round_index in range(spec.degraded_rounds):
                    result = await self._run_round(
                        runtime,
                        workspace,
                        phase="degraded",
                        round_index=round_index,
                        job_count=spec.worker_count - 1,
                        now=fault_time,
                    )
                    degraded_samples.extend(result.dispatch_samples)
                    self._record_round(result, placement_counts, worker_job_ids, run_ids)
                    degraded_terminal_jobs += sum(
                        record.state is DispatchState.TERMINAL for record in result.records
                    )
                    degraded_workers.update(record.worker_id for record in result.records)

                replacement = DistributedWorkerProcess(
                    DistributedWorkerProcessConfig(
                        registration=victim.registration,
                        worker_id=victim_id,
                        workspace_root=victim.workspace_root,
                        reporting=False,
                    ),
                    protocol=None,
                    transport=transport,
                )
                processes[victim_id] = replacement
                process_tasks[victim_id] = asyncio.create_task(replacement.run())
                await asyncio.sleep(0)
                await asyncio.sleep(0)

                rejoin_time = fault_time + timedelta(milliseconds=10)
                rejoin_started = time.perf_counter()
                receipt = await service.register(
                    victim.registration,
                    _credentials(
                        victim.token,
                        nonce="fault-rejoin-register",
                        correlation_id="fault-rejoin-register",
                    ),
                    now=rejoin_time,
                )
                rejoin_samples.append(time.perf_counter() - rejoin_started)
                sequences[victim_id] = 1
                await service.heartbeat(
                    WorkerHeartbeatRequest(
                        heartbeat=Heartbeat(
                            node_id=victim.node.node_id,
                            sequence=1,
                            resources=victim.node.resources,
                            workers=(victim.worker,),
                        ),
                        service_identity_ref=victim_id,
                    ),
                    _credentials(
                        victim.token,
                        nonce="fault-rejoin-heartbeat",
                        correlation_id="fault-rejoin-heartbeat",
                    ),
                    now=rejoin_time,
                )
                rejoined_worker_online = (
                    runtime.registry.get_worker(victim_id).status is WorkerStatus.ONLINE
                )
                rejoin_preserved_identity = (
                    receipt.node_id == victim.node.node_id
                    and receipt.worker_ids == (victim_id,)
                    and tuple(worker.worker_id for worker in runtime.registry.list_workers())
                    == tuple(sorted(initial_worker_ids))
                )
                if not rejoined_worker_online or not rejoin_preserved_identity:
                    errors.append("Worker rejoin did not preserve canonical identity and liveness")

                for round_index in range(spec.post_rejoin_rounds):
                    result = await self._run_round(
                        runtime,
                        workspace,
                        phase="post-rejoin",
                        round_index=round_index,
                        job_count=spec.worker_count,
                        now=rejoin_time,
                    )
                    post_rejoin_samples.extend(result.dispatch_samples)
                    self._record_round(result, placement_counts, worker_job_ids, run_ids)
                    post_rejoin_terminal_jobs += sum(
                        record.state is DispatchState.TERMINAL for record in result.records
                    )
                    post_rejoin_workers.update(record.worker_id for record in result.records)

                outage_time = rejoin_time + timedelta(milliseconds=10)
                failure_job = _job(workspace, phase="workspace-outage", round_index=0, ordinal=0)
                worker_job_ids.append(failure_job.worker_job_id)
                run_ids.append(failure_job.execution.run_id)
                await transport.set_available(False)
                failure_started = time.perf_counter()
                try:
                    await runtime.dispatch(failure_job, now=outage_time)
                except ContractError as exc:
                    workspace_failure_samples.append(time.perf_counter() - failure_started)
                    workspace_failure_observed = True
                    workspace_failure_code = exc.code.value
                    workspace_failure_retryable = exc.retryable
                except Exception as exc:
                    workspace_failure_samples.append(time.perf_counter() - failure_started)
                    errors.append(
                        "Workspace outage raised non-canonical error: "
                        f"{type(exc).__name__}: {exc}"
                    )
                else:
                    workspace_failure_samples.append(time.perf_counter() - failure_started)
                    errors.append("Workspace outage unexpectedly accepted dispatch")
                finally:
                    await transport.set_available(True)

                try:
                    failed_record = runtime.get_record(failure_job.worker_job_id)
                except Exception:
                    failed_record = None
                if failed_record is not None:
                    workspace_failure_record_lost = failed_record.state is DispatchState.LOST
                    workspace_failure_reached_execution = failed_record.handle is not None

                recovery_time = outage_time + timedelta(
                    seconds=spec.reservation_ttl_seconds + 0.1
                )
                for index, fixture in enumerate(worker_fixtures):
                    worker_id = fixture.worker.worker_id
                    sequences[worker_id] += 1
                    await service.heartbeat(
                        WorkerHeartbeatRequest(
                            heartbeat=Heartbeat(
                                node_id=fixture.node.node_id,
                                sequence=sequences[worker_id],
                                resources=fixture.node.resources,
                                workers=(fixture.worker,),
                            ),
                            service_identity_ref=worker_id,
                        ),
                        _credentials(
                            fixture.token,
                            nonce=f"fault-recovery-heartbeat-{index}",
                            correlation_id=f"fault-recovery-heartbeat-{index}",
                        ),
                        now=recovery_time,
                    )
                await runtime.reconcile(now=recovery_time)

                recovery_job = _job(workspace, phase="workspace-recovery", round_index=0, ordinal=0)
                worker_job_ids.append(recovery_job.worker_job_id)
                run_ids.append(recovery_job.execution.run_id)
                recovery_started = time.perf_counter()
                recovery_record = await runtime.dispatch(recovery_job, now=recovery_time)
                workspace_recovery_samples.append(time.perf_counter() - recovery_started)
                placement_counts[recovery_record.worker_id] += 1
                reconciled = await runtime.reconcile(now=recovery_time)
                recovery_record = next(
                    record
                    for record in reconciled
                    if record.job.worker_job_id == recovery_job.worker_job_id
                )
                result = await runtime.result(recovery_job.worker_job_id)
                workspace_recovery_terminal = (
                    recovery_record.state is DispatchState.TERMINAL and result is not None
                )
                if not workspace_recovery_terminal:
                    errors.append("Workspace recovery job did not terminate successfully")
        except TimeoutError:
            errors.append("distributed fault benchmark exceeded timeout")
        except Exception as exc:
            errors.append(f"distributed fault benchmark failed: {type(exc).__name__}: {exc}")
        finally:
            duration = max(0.0, time.perf_counter() - started)
            traced_current, traced_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            for process in processes.values():
                process.stop()
            await asyncio.gather(*process_tasks.values(), return_exceptions=True)
            await transport.close(graceful=False)

        storage_after = _directory_size(self._data_dir)
        workspace_cleanup_succeeded = all(
            not (fixture.workspace_root / workspace.workspace_id / workspace.snapshot_id).exists()
            for fixture in worker_fixtures
        )
        terminal_successful_jobs = sum(
            record.state is DispatchState.TERMINAL for record in runtime.records()
        )
        duplicate_worker_job_ids = len(worker_job_ids) - len(set(worker_job_ids))
        duplicate_run_ids = len(run_ids) - len(set(run_ids))
        degraded_avoided_lost_worker = (
            degraded_terminal_jobs == spec.degraded_jobs
            and victim_id not in degraded_workers
            and len(degraded_workers) == spec.worker_count - 1
        )
        post_rejoin_used_rejoined_worker = (
            post_rejoin_terminal_jobs == spec.post_rejoin_jobs
            and victim_id in post_rejoin_workers
            and len(post_rejoin_workers) == spec.worker_count
        )
        stable_worker_ids = len(
            set(worker.worker_id for worker in runtime.registry.list_workers())
            & set(initial_worker_ids)
        )
        correctness = DistributedFaultCorrectnessSummary(
            expected_workers=spec.worker_count,
            stable_worker_ids=stable_worker_ids,
            lost_worker_offline=lost_worker_offline,
            degraded_jobs=spec.degraded_jobs,
            degraded_terminal_jobs=degraded_terminal_jobs,
            degraded_avoided_lost_worker=degraded_avoided_lost_worker,
            rejoined_worker_online=rejoined_worker_online,
            rejoin_preserved_identity=rejoin_preserved_identity,
            post_rejoin_jobs=spec.post_rejoin_jobs,
            post_rejoin_terminal_jobs=post_rejoin_terminal_jobs,
            post_rejoin_used_rejoined_worker=post_rejoin_used_rejoined_worker,
            workspace_failure_observed=workspace_failure_observed,
            workspace_failure_code=workspace_failure_code,
            workspace_failure_retryable=workspace_failure_retryable,
            workspace_failure_record_lost=workspace_failure_record_lost,
            workspace_failure_reached_execution=workspace_failure_reached_execution,
            workspace_recovery_terminal=workspace_recovery_terminal,
            workspace_cleanup_succeeded=workspace_cleanup_succeeded,
            expected_successful_jobs=spec.expected_successful_jobs,
            terminal_successful_jobs=terminal_successful_jobs,
            duplicate_worker_job_ids=duplicate_worker_job_ids,
            duplicate_run_ids=duplicate_run_ids,
            passed=(
                not errors
                and stable_worker_ids == spec.worker_count
                and lost_worker_offline
                and degraded_avoided_lost_worker
                and rejoined_worker_online
                and rejoin_preserved_identity
                and post_rejoin_used_rejoined_worker
                and workspace_failure_observed
                and workspace_failure_code == ErrorCode.UNAVAILABLE.value
                and workspace_failure_retryable
                and workspace_failure_record_lost
                and not workspace_failure_reached_execution
                and workspace_recovery_terminal
                and workspace_cleanup_succeeded
                and terminal_successful_jobs == spec.expected_successful_jobs
                and duplicate_worker_job_ids == 0
                and duplicate_run_ids == 0
            ),
        )
        if not correctness.passed and not errors:
            errors.append("distributed fault correctness invariants failed")

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
        throughput = spec.expected_successful_jobs / duration if duration > 0 else 0.0
        return DistributedFaultReport(
            schema_version=DISTRIBUTED_FAULT_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at.isoformat(),
            duration_seconds=round(duration, 6),
            environment=_environment_metadata(),
            throughput_successful_jobs_per_second=round(throughput, 6),
            pre_fault_dispatch_latency=LatencyDistribution.from_seconds(pre_fault_samples),
            worker_loss_reconciliation_latency=LatencyDistribution.from_seconds(loss_samples),
            degraded_dispatch_latency=LatencyDistribution.from_seconds(degraded_samples),
            worker_rejoin_latency=LatencyDistribution.from_seconds(rejoin_samples),
            post_rejoin_dispatch_latency=LatencyDistribution.from_seconds(post_rejoin_samples),
            workspace_failure_latency=LatencyDistribution.from_seconds(workspace_failure_samples),
            workspace_recovery_dispatch_latency=LatencyDistribution.from_seconds(
                workspace_recovery_samples
            ),
            placement_counts=dict(sorted(placement_counts.items())),
            resources=resources,
            correctness=correctness,
            lost_worker_id=victim_id,
            worker_ids=tuple(sorted(initial_worker_ids)),
            worker_job_ids=tuple(worker_job_ids),
            run_ids=tuple(run_ids),
            errors=tuple(errors),
        )

    async def _run_round(
        self,
        runtime: DistributedRuntime,
        workspace: Any,
        *,
        phase: str,
        round_index: int,
        job_count: int,
        now: datetime,
    ) -> _RoundResult:
        jobs = tuple(
            _job(workspace, phase=phase, round_index=round_index, ordinal=ordinal)
            for ordinal in range(job_count)
        )
        dispatched = await asyncio.gather(
            *(self._dispatch_timed_at(runtime, job, now=now) for job in jobs)
        )
        reconciled = await runtime.reconcile(now=now)
        current_ids = {job.worker_job_id for job in jobs}
        current_records = tuple(
            record for record in reconciled if record.job.worker_job_id in current_ids
        )
        for record in current_records:
            await runtime.result(record.job.worker_job_id)
        return _RoundResult(
            records=current_records,
            dispatch_samples=tuple(elapsed for _record, elapsed in dispatched),
            worker_job_ids=tuple(job.worker_job_id for job in jobs),
            run_ids=tuple(job.execution.run_id for job in jobs),
        )

    @staticmethod
    async def _dispatch_timed_at(
        runtime: DistributedRuntime,
        job: WorkerJobRequest,
        *,
        now: datetime,
    ) -> tuple[DispatchRecord, float]:
        started = time.perf_counter()
        record = await runtime.dispatch(job, now=now)
        return record, time.perf_counter() - started

    @staticmethod
    def _record_round(
        result: _RoundResult,
        placement_counts: Counter[str],
        worker_job_ids: list[str],
        run_ids: list[str],
    ) -> None:
        for record in result.records:
            placement_counts[record.worker_id] += 1
        worker_job_ids.extend(result.worker_job_ids)
        run_ids.extend(result.run_ids)


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
    workspace: Any,
    *,
    phase: str,
    round_index: int,
    ordinal: int,
) -> WorkerJobRequest:
    task_id = f"task_{phase.replace('-', '_')}_{round_index}_{ordinal}_{time.time_ns()}"
    run_id = f"run_{phase.replace('-', '_')}_{round_index}_{ordinal}_{time.time_ns()}"
    key = f"distributed-fault:{phase}:{round_index}:{ordinal}:{run_id}"
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=run_id,
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(
                correlation_id=key,
                project_id=workspace.project_id,
            ),
            input={"plan_ref": phase},
        ),
        workspace_ref=workspace.workspace_id,
        snapshot_ref=workspace.snapshot_id,
        idempotency_key=key,
    )


def _json_compatible(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    return value
