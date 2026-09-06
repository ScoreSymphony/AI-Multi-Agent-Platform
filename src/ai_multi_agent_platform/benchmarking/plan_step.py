"""Durable Plan/Step scale evidence for issue #440 after #384."""

from __future__ import annotations

import asyncio
import time
import tracemalloc
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.contracts import PlanRequest, PlanResponse, PlanStepProposal
from ai_multi_agent_platform.coordination import (
    DurablePlanStepCoordinator,
    PlanCoordinationProjection,
    SQLiteCoordinatorRepository,
)
from ai_multi_agent_platform.domain import Plan, RunStatus, Step, StepStatus, TaskStatus, new_id
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.kernel.models import TaskState
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

from .models import LatencyDistribution, ResourceMetrics
from .single_node import (
    _directory_size,
    _environment_metadata,
    _open_file_descriptor_count,
    _peak_rss_bytes,
    _require_fresh_data_root,
)

PLAN_STEP_REPORT_SCHEMA_VERSION = "1.0"
PlanStepScenario = Literal["linear", "fan-out", "fan-in"]


@dataclass(frozen=True, slots=True)
class PlanStepBenchmarkSpec:
    """One bounded deterministic durable coordination profile."""

    scenario: PlanStepScenario
    size: int
    timeout_seconds: float = 30.0
    safety_max_size: int = 2048
    benchmark_id: str = "single-node.plan-step-scale"
    benchmark_version: str = "1.0"
    deployment_profile: str = "single-node-reference"
    persistence_profile: str = "sqlite-kernel+coordinator"

    def __post_init__(self) -> None:
        if self.scenario not in {"linear", "fan-out", "fan-in"}:
            raise ValueError(f"unsupported plan-step scenario: {self.scenario}")
        if self.size < 1:
            raise ValueError("size must be at least 1")
        if self.safety_max_size < 1:
            raise ValueError("safety_max_size must be at least 1")
        if self.size > self.safety_max_size:
            raise ValueError("size exceeds configured plan-step safety bound")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    @property
    def expected_step_count(self) -> int:
        if self.scenario == "linear":
            return self.size
        if self.scenario == "fan-out":
            return self.size + 1
        return self.size + 2

    @property
    def expected_peak_active_width(self) -> int:
        return 1 if self.scenario == "linear" else self.size

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "expected_step_count": self.expected_step_count,
            "expected_peak_active_width": self.expected_peak_active_width,
            "expected_invariants": [
                "one canonical Run is created for every Step",
                "dependencies complete before dependent Steps",
                "all Steps and the owning Task reach succeeded",
                "fan-out/fan-in activation width matches the requested deterministic graph",
            ],
            "captured_metrics": [
                "registration latency p50/p95/p99",
                "kernel run-outcome persistence latency p50/p95/p99",
                "coordinator observation latency p50/p95/p99",
                "completed Steps per second",
                "peak active Step width",
                "process CPU and traced memory",
                "SQLite storage growth",
            ],
        }


@dataclass(frozen=True, slots=True)
class PlanStepCorrectnessSummary:
    expected_steps: int
    succeeded_steps: int
    run_created_events: int
    unique_run_ids: int
    dependency_order_valid: bool
    task_succeeded: bool
    active_width_peak: int
    expected_active_width_peak: int
    passed: bool


@dataclass(frozen=True, slots=True)
class PlanStepBenchmarkReport:
    schema_version: str
    benchmark: PlanStepBenchmarkSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: dict[str, Any]
    throughput_steps_per_second: float
    registration_latency: LatencyDistribution
    outcome_persistence_latency: LatencyDistribution
    coordination_observation_latency: LatencyDistribution
    resources: ResourceMetrics
    correctness: PlanStepCorrectnessSummary
    task_id: str
    plan_id: str
    step_ids: tuple[str, ...]
    run_ids: tuple[str, ...]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            _json_compatible(
                {
                    "schema_version": self.schema_version,
                    "benchmark": self.benchmark.to_dict(),
                    "platform_version": self.platform_version,
                    "platform_commit": self.platform_commit,
                    "started_at": self.started_at,
                    "duration_seconds": self.duration_seconds,
                    "environment": self.environment,
                    "throughput_steps_per_second": self.throughput_steps_per_second,
                    "registration_latency": asdict(self.registration_latency),
                    "outcome_persistence_latency": asdict(self.outcome_persistence_latency),
                    "coordination_observation_latency": asdict(
                        self.coordination_observation_latency
                    ),
                    "resources": asdict(self.resources),
                    "correctness": asdict(self.correctness),
                    "task_id": self.task_id,
                    "plan_id": self.plan_id,
                    "step_ids": self.step_ids,
                    "run_ids": self.run_ids,
                    "errors": self.errors,
                }
            ),
        )


class _GraphOrchestrator(FakeOrchestrator):
    """Deterministic planning fixture behind the canonical Orchestrator seam."""

    def __init__(self, scenario: PlanStepScenario, size: int) -> None:
        super().__init__(summary_prefix="Deterministic plan-step benchmark")
        self.proposals = _graph_proposals(scenario, size)

    async def plan(self, request: PlanRequest) -> PlanResponse:
        self.calls.append(request)
        return PlanResponse(
            summary=f"Deterministic {len(self.proposals)}-Step {request.objective}",
            steps=self.proposals,
        )


class PlanStepBenchmarkHarness:
    """Exercise canonical kernel + durable #384 coordinator paths with SQLite persistence."""

    def __init__(self, data_dir: Path, *, platform_commit: str = "unknown") -> None:
        self._data_dir = data_dir
        self._platform_commit = platform_commit

    async def run(self, spec: PlanStepBenchmarkSpec) -> PlanStepBenchmarkReport:
        _require_fresh_data_root(self._data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        db_dir = self._data_dir / "db"
        db_dir.mkdir(parents=True, exist_ok=True)

        orchestrator = _GraphOrchestrator(spec.scenario, spec.size)
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=orchestrator,
            lifecycle=lifecycle,
            repository=SqliteKernelRepository(db_dir / "kernel.sqlite3"),
        )
        coordinator = DurablePlanStepCoordinator(
            repository=SQLiteCoordinatorRepository(db_dir / "coordination.sqlite3"),
            kernel=kernel,
            coordinator_id="benchmark-plan-step",
        )
        planned = await _plan_task(kernel, orchestrator, spec.timeout_seconds)
        plan, steps = _materialize_plan(planned, orchestrator.proposals)

        storage_before = _directory_size(self._data_dir)
        tracing_was_active = tracemalloc.is_tracing()
        if not tracing_was_active:
            tracemalloc.start()
        cpu_before = time.process_time()
        wall_started = time.perf_counter()
        started_at = datetime.now(UTC).isoformat()

        registration_samples: list[float] = []
        outcome_samples: list[float] = []
        observation_samples: list[float] = []
        completion_order: list[str] = []
        errors: list[str] = []
        active_width_peak = 0

        try:
            started = time.perf_counter()
            projection = await asyncio.wait_for(
                coordinator.register_plan(plan, steps),
                timeout=spec.timeout_seconds,
            )
            registration_samples.append(time.perf_counter() - started)
            active_width_peak = max(active_width_peak, _active_width(projection))

            while any(item.status is not StepStatus.SUCCEEDED for item in projection.steps):
                running = tuple(
                    item for item in projection.steps if item.status is StepStatus.RUNNING
                )
                active_width_peak = max(active_width_peak, len(running))
                if not running:
                    errors.append("coordination stalled with unfinished Steps and no active Run")
                    break

                for item in running:
                    if item.latest_run_id is None:
                        errors.append(f"running Step {item.step_id} has no canonical Run")
                        continue
                    run_id = item.latest_run_id

                    outcome_started = time.perf_counter()
                    await asyncio.wait_for(
                        kernel.record_run_outcome(
                            idempotency_key=f"benchmark:{run_id}:succeeded",
                            task_id=plan.task_id,
                            run_id=run_id,
                            status=RunStatus.SUCCEEDED,
                        ),
                        timeout=spec.timeout_seconds,
                    )
                    outcome_samples.append(time.perf_counter() - outcome_started)

                    observe_started = time.perf_counter()
                    projection = await asyncio.wait_for(
                        coordinator.observe_run(
                            task_id=plan.task_id,
                            run_id=run_id,
                            observation_key=f"benchmark:{run_id}:observed",
                        ),
                        timeout=spec.timeout_seconds,
                    )
                    observation_samples.append(time.perf_counter() - observe_started)
                    completion_order.append(item.step_id)
                    active_width_peak = max(active_width_peak, _active_width(projection))
        except Exception as exc:  # benchmark evidence must retain deterministic failure details
            errors.append(f"{type(exc).__name__}: {exc}")

        duration = time.perf_counter() - wall_started
        process_cpu_seconds = time.process_time() - cpu_before
        traced_current, traced_peak = tracemalloc.get_traced_memory()
        if not tracing_was_active:
            tracemalloc.stop()
        storage_after = _directory_size(self._data_dir)

        final_projection = coordinator.projection(plan.id)
        final_task = await kernel.get_task(plan.task_id)
        history = await kernel.history(plan.task_id)
        run_created_events = sum(event.event_type == "run.created" for event in history)
        run_ids = tuple(
            item.latest_run_id for item in final_projection.steps if item.latest_run_id is not None
        )
        succeeded_steps = sum(
            item.status is StepStatus.SUCCEEDED for item in final_projection.steps
        )
        dependency_order_valid = _dependency_order_valid(steps, completion_order)
        correctness = PlanStepCorrectnessSummary(
            expected_steps=spec.expected_step_count,
            succeeded_steps=succeeded_steps,
            run_created_events=run_created_events,
            unique_run_ids=len(set(run_ids)),
            dependency_order_valid=dependency_order_valid,
            task_succeeded=final_task.status is TaskStatus.SUCCEEDED,
            active_width_peak=active_width_peak,
            expected_active_width_peak=spec.expected_peak_active_width,
            passed=(
                not errors
                and len(final_projection.steps) == spec.expected_step_count
                and succeeded_steps == spec.expected_step_count
                and run_created_events == spec.expected_step_count
                and len(run_ids) == spec.expected_step_count
                and len(set(run_ids)) == spec.expected_step_count
                and dependency_order_valid
                and final_task.status is TaskStatus.SUCCEEDED
                and active_width_peak == spec.expected_peak_active_width
            ),
        )
        throughput = succeeded_steps / duration if duration > 0 else 0.0
        return PlanStepBenchmarkReport(
            schema_version=PLAN_STEP_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at,
            duration_seconds=round(duration, 6),
            environment=_environment_metadata(),
            throughput_steps_per_second=round(throughput, 6),
            registration_latency=LatencyDistribution.from_seconds(registration_samples),
            outcome_persistence_latency=LatencyDistribution.from_seconds(outcome_samples),
            coordination_observation_latency=LatencyDistribution.from_seconds(observation_samples),
            resources=ResourceMetrics(
                process_cpu_seconds=round(process_cpu_seconds, 6),
                traced_memory_current_bytes=traced_current,
                traced_memory_peak_bytes=traced_peak,
                peak_rss_bytes=_peak_rss_bytes(),
                storage_bytes_before=storage_before,
                storage_bytes_after=storage_after,
                storage_growth_bytes=storage_after - storage_before,
                open_file_descriptors=_open_file_descriptor_count(),
            ),
            correctness=correctness,
            task_id=plan.task_id,
            plan_id=plan.id,
            step_ids=tuple(step.id for step in steps),
            run_ids=run_ids,
            errors=tuple(errors),
        )


async def _plan_task(
    kernel: PlatformKernel,
    orchestrator: _GraphOrchestrator,
    timeout_seconds: float,
) -> TaskState:
    project_id = new_id("project")
    created = await asyncio.wait_for(
        kernel.create_task(
            idempotency_key="benchmark:plan-step:create",
            title="Durable Plan/Step benchmark",
            objective="measure deterministic durable coordination overhead",
            owner_type="service",
            owner_id="benchmark-plan-step",
            project_id=project_id,
        ),
        timeout=timeout_seconds,
    )
    await asyncio.wait_for(
        kernel.ready_task(
            idempotency_key="benchmark:plan-step:ready",
            task_id=created.task_id,
        ),
        timeout=timeout_seconds,
    )
    planned = await asyncio.wait_for(
        kernel.plan_task(
            idempotency_key="benchmark:plan-step:plan",
            task_id=created.task_id,
        ),
        timeout=timeout_seconds,
    )
    if len(orchestrator.calls) != 1:
        raise RuntimeError(
            "benchmark planning did not use the canonical Orchestrator seam exactly once"
        )
    return planned


def _materialize_plan(
    planned: TaskState,
    proposals: tuple[PlanStepProposal, ...],
) -> tuple[Plan, tuple[Step, ...]]:
    if planned.plan_ref is None:
        raise RuntimeError("canonical planning produced no Plan ID")
    if len(planned.step_ids) != len(proposals):
        raise RuntimeError("canonical planning Step count differs from deterministic proposal")
    ids_by_key = {
        proposal.key: step_id for proposal, step_id in zip(proposals, planned.step_ids, strict=True)
    }
    plan = Plan(
        id=planned.plan_ref,
        task_id=planned.task_id,
        owner_ref=planned.task.owner_ref,
        active=True,
        project_id=planned.task.project_id,
    )
    steps = tuple(
        Step(
            id=ids_by_key[proposal.key],
            plan_id=plan.id,
            title=proposal.title,
            owner_ref=planned.task.owner_ref,
            depends_on=tuple(ids_by_key[key] for key in proposal.depends_on),
            project_id=planned.task.project_id,
        )
        for proposal in proposals
    )
    return plan, steps


def _graph_proposals(
    scenario: PlanStepScenario,
    size: int,
) -> tuple[PlanStepProposal, ...]:
    if scenario == "linear":
        return tuple(
            PlanStepProposal(
                key=f"linear-{index}",
                title=f"Linear Step {index}",
                objective="advance deterministic linear coordination",
                depends_on=() if index == 0 else (f"linear-{index - 1}",),
            )
            for index in range(size)
        )

    root = PlanStepProposal(
        key="root",
        title="Fan root",
        objective="activate deterministic fan width",
    )
    leaves = tuple(
        PlanStepProposal(
            key=f"leaf-{index}",
            title=f"Fan leaf {index}",
            objective="execute deterministic fan leaf",
            depends_on=("root",),
        )
        for index in range(size)
    )
    if scenario == "fan-out":
        return (root, *leaves)
    barrier = PlanStepProposal(
        key="barrier",
        title="Fan-in barrier",
        objective="join deterministic fan leaves",
        depends_on=tuple(leaf.key for leaf in leaves),
    )
    return (root, *leaves, barrier)


def _active_width(projection: PlanCoordinationProjection) -> int:
    return sum(item.status is StepStatus.RUNNING for item in projection.steps)


def _dependency_order_valid(steps: tuple[Step, ...], completion_order: list[str]) -> bool:
    positions = {step_id: index for index, step_id in enumerate(completion_order)}
    if len(positions) != len(steps):
        return False
    return all(
        positions[dependency_id] < positions[step.id]
        for step in steps
        for dependency_id in step.depends_on
    )


def _json_compatible(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    return value
