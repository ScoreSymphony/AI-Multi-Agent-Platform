"""Retry, wait and reconciliation pressure evidence for issue #440."""

from __future__ import annotations

import asyncio
import time
import tracemalloc
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.contracts import PlanRequest, PlanResponse, PlanStepProposal
from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    DurablePlanStepCoordinator,
    PlanCoordinationProjection,
    SQLiteCoordinatorRepository,
    StepRetryPolicy,
    StepWait,
    WaitType,
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

COORDINATION_PRESSURE_REPORT_SCHEMA_VERSION = "1.0"
CoordinationPressureScenario = Literal[
    "retry-burst",
    "deadline-wait-burst",
    "restart-reconcile",
]


@dataclass(frozen=True, slots=True)
class CoordinationPressureSpec:
    """One bounded durable coordination pressure/recovery profile."""

    scenario: CoordinationPressureScenario
    size: int
    retry_delay_seconds: float = 1.0
    wait_delay_seconds: float = 1.0
    timeout_seconds: float = 30.0
    safety_max_size: int = 1024
    benchmark_id: str = "single-node.coordination-pressure"
    benchmark_version: str = "1.0"
    deployment_profile: str = "single-node-reference"
    persistence_profile: str = "sqlite-kernel+coordinator"

    def __post_init__(self) -> None:
        if self.scenario not in {
            "retry-burst",
            "deadline-wait-burst",
            "restart-reconcile",
        }:
            raise ValueError(f"unsupported coordination pressure scenario: {self.scenario}")
        if self.size < 1:
            raise ValueError("size must be at least 1")
        if self.safety_max_size < 1:
            raise ValueError("safety_max_size must be at least 1")
        if self.size > self.safety_max_size:
            raise ValueError("size exceeds configured coordination pressure safety bound")
        if self.retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must not be negative")
        if self.wait_delay_seconds < 0:
            raise ValueError("wait_delay_seconds must not be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    @property
    def expected_runs(self) -> int:
        return self.size * 2 if self.scenario == "retry-burst" else self.size

    @property
    def expected_attempt(self) -> int:
        return 2 if self.scenario == "retry-burst" else 1

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "expected_runs": self.expected_runs,
            "expected_attempt": self.expected_attempt,
            "expected_invariants": [
                "every configured Step reaches succeeded",
                "canonical Run identities are never duplicated during wakeup or reconciliation",
                "retry wakeup creates one new attempt per Step only after the persisted due time",
                "deadline wakeup resumes the existing active attempt without creating another Run",
                "restart reconciliation preserves every active canonical Run identity",
            ],
            "captured_metrics": [
                "pressure transition latency p50/p95/p99",
                "batch wakeup or reconciliation latency p50/p95/p99",
                "kernel run-outcome persistence latency p50/p95/p99",
                "coordinator observation latency p50/p95/p99",
                "completed Steps per second",
                "process CPU and traced memory",
                "SQLite storage growth",
            ],
        }


@dataclass(frozen=True, slots=True)
class CoordinationPressureCorrectnessSummary:
    expected_steps: int
    succeeded_steps: int
    expected_runs: int
    run_created_events: int
    unique_run_ids: int
    expected_attempt: int
    maximum_attempt: int
    retry_scheduled_steps: int
    wait_entered_steps: int
    wait_resolved_steps: int
    reconciled_running_steps: int
    run_identity_preserved: bool
    task_succeeded: bool
    passed: bool


@dataclass(frozen=True, slots=True)
class CoordinationPressureReport:
    schema_version: str
    benchmark: CoordinationPressureSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: dict[str, Any]
    throughput_steps_per_second: float
    transition_latency: LatencyDistribution
    resume_or_reconcile_latency: LatencyDistribution
    outcome_persistence_latency: LatencyDistribution
    coordination_observation_latency: LatencyDistribution
    resources: ResourceMetrics
    correctness: CoordinationPressureCorrectnessSummary
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
                    "transition_latency": asdict(self.transition_latency),
                    "resume_or_reconcile_latency": asdict(self.resume_or_reconcile_latency),
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


class _IndependentOrchestrator(FakeOrchestrator):
    """Create proposal-local independent Steps behind the canonical planner seam."""

    def __init__(self, size: int) -> None:
        super().__init__(summary_prefix="Deterministic coordination pressure")
        self.proposals = tuple(
            PlanStepProposal(
                key=f"step-{index}",
                title=f"Pressure Step {index}",
                objective="exercise durable coordination pressure",
            )
            for index in range(size)
        )

    async def plan(self, request: PlanRequest) -> PlanResponse:
        self.calls.append(request)
        return PlanResponse(
            summary=f"Deterministic {len(self.proposals)}-Step {request.objective}",
            steps=self.proposals,
        )


class CoordinationPressureHarness:
    """Exercise persisted retry/wait/restart seams without bypassing canonical lifecycle."""

    def __init__(self, data_dir: Path, *, platform_commit: str = "unknown") -> None:
        self._data_dir = data_dir
        self._platform_commit = platform_commit

    async def run(self, spec: CoordinationPressureSpec) -> CoordinationPressureReport:
        _require_fresh_data_root(self._data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        db_dir = self._data_dir / "db"
        db_dir.mkdir(parents=True, exist_ok=True)
        kernel_path = db_dir / "kernel.sqlite3"
        coordination_path = db_dir / "coordination.sqlite3"

        orchestrator = _IndependentOrchestrator(spec.size)
        lifecycle = FakeLifecycleBackend()
        kernel = _kernel(kernel_path, orchestrator, lifecycle)
        coordinator = DurablePlanStepCoordinator(
            repository=SQLiteCoordinatorRepository(coordination_path),
            kernel=kernel,
            coordinator_id="benchmark-coordination-pressure-A",
        )
        planned = await _plan_task(kernel, orchestrator, spec.timeout_seconds)
        plan, steps = _materialize_plan(planned, orchestrator.proposals)

        retry_policies = None
        if spec.scenario == "retry-burst":
            retry_policies = {
                step.id: StepRetryPolicy(
                    max_attempts=2,
                    initial_delay_seconds=spec.retry_delay_seconds,
                    multiplier=1,
                    max_delay_seconds=spec.retry_delay_seconds,
                    retryable_categories=("benchmark-transient",),
                )
                for step in steps
            }

        projection = await asyncio.wait_for(
            coordinator.register_plan(plan, steps, retry_policies=retry_policies),
            timeout=spec.timeout_seconds,
        )
        initial_run_ids = _running_run_ids(projection)
        if len(initial_run_ids) != spec.size:
            raise RuntimeError("initial coordination did not activate every independent Step")

        storage_before = _directory_size(self._data_dir)
        tracing_was_active = tracemalloc.is_tracing()
        if not tracing_was_active:
            tracemalloc.start()
        cpu_before = time.process_time()
        wall_started = time.perf_counter()
        started_at = datetime.now(UTC).isoformat()

        transition_samples: list[float] = []
        resume_samples: list[float] = []
        outcome_samples: list[float] = []
        observation_samples: list[float] = []
        retry_scheduled: set[str] = set()
        wait_entered: set[str] = set()
        wait_resolved: set[str] = set()
        reconciled_running: set[str] = set()
        run_identity_preserved = True
        errors: list[str] = []
        current_kernel = kernel
        current_coordinator = coordinator

        try:
            if spec.scenario == "retry-burst":
                current_coordinator, projection = await self._retry_burst(
                    spec=spec,
                    plan=plan,
                    steps=steps,
                    kernel=current_kernel,
                    coordinator=current_coordinator,
                    projection=projection,
                    transition_samples=transition_samples,
                    resume_samples=resume_samples,
                    outcome_samples=outcome_samples,
                    observation_samples=observation_samples,
                    retry_scheduled=retry_scheduled,
                )
            elif spec.scenario == "deadline-wait-burst":
                current_coordinator, projection = await self._deadline_wait_burst(
                    spec=spec,
                    plan=plan,
                    steps=steps,
                    kernel=current_kernel,
                    coordinator=current_coordinator,
                    projection=projection,
                    transition_samples=transition_samples,
                    resume_samples=resume_samples,
                    outcome_samples=outcome_samples,
                    observation_samples=observation_samples,
                    wait_entered=wait_entered,
                    wait_resolved=wait_resolved,
                )
            else:
                (
                    current_kernel,
                    current_coordinator,
                    projection,
                    run_identity_preserved,
                ) = await self._restart_reconcile(
                    spec=spec,
                    plan=plan,
                    steps=steps,
                    orchestrator=orchestrator,
                    lifecycle=lifecycle,
                    kernel_path=kernel_path,
                    coordination_path=coordination_path,
                    initial_run_ids=initial_run_ids,
                    resume_samples=resume_samples,
                    outcome_samples=outcome_samples,
                    observation_samples=observation_samples,
                    reconciled_running=reconciled_running,
                )
        except Exception as exc:  # evidence must retain deterministic failure details
            errors.append(f"{type(exc).__name__}: {exc}")

        duration = time.perf_counter() - wall_started
        process_cpu_seconds = time.process_time() - cpu_before
        traced_current, traced_peak = tracemalloc.get_traced_memory()
        if not tracing_was_active:
            tracemalloc.stop()
        storage_after = _directory_size(self._data_dir)

        final_projection = current_coordinator.projection(plan.id)
        final_task = await current_kernel.get_task(plan.task_id)
        history = await current_kernel.history(plan.task_id)
        run_created_events = sum(event.event_type == "run.created" for event in history)
        final_run_ids = tuple(
            item.latest_run_id for item in final_projection.steps if item.latest_run_id is not None
        )
        succeeded_steps = sum(
            item.status is StepStatus.SUCCEEDED for item in final_projection.steps
        )
        maximum_attempt = max((item.current_attempt for item in final_projection.steps), default=0)

        correctness = CoordinationPressureCorrectnessSummary(
            expected_steps=spec.size,
            succeeded_steps=succeeded_steps,
            expected_runs=spec.expected_runs,
            run_created_events=run_created_events,
            unique_run_ids=len(set(_history_run_ids(history))),
            expected_attempt=spec.expected_attempt,
            maximum_attempt=maximum_attempt,
            retry_scheduled_steps=len(retry_scheduled),
            wait_entered_steps=len(wait_entered),
            wait_resolved_steps=len(wait_resolved),
            reconciled_running_steps=len(reconciled_running),
            run_identity_preserved=run_identity_preserved,
            task_succeeded=final_task.status is TaskStatus.SUCCEEDED,
            passed=(
                not errors
                and succeeded_steps == spec.size
                and len(final_projection.steps) == spec.size
                and run_created_events == spec.expected_runs
                and len(set(_history_run_ids(history))) == spec.expected_runs
                and maximum_attempt == spec.expected_attempt
                and final_task.status is TaskStatus.SUCCEEDED
                and _scenario_specific_passed(
                    spec,
                    retry_scheduled=retry_scheduled,
                    wait_entered=wait_entered,
                    wait_resolved=wait_resolved,
                    reconciled_running=reconciled_running,
                    run_identity_preserved=run_identity_preserved,
                )
            ),
        )
        throughput = succeeded_steps / duration if duration > 0 else 0.0
        return CoordinationPressureReport(
            schema_version=COORDINATION_PRESSURE_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at,
            duration_seconds=round(duration, 6),
            environment=_environment_metadata(),
            throughput_steps_per_second=round(throughput, 6),
            transition_latency=LatencyDistribution.from_seconds(transition_samples),
            resume_or_reconcile_latency=LatencyDistribution.from_seconds(resume_samples),
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
            run_ids=final_run_ids,
            errors=tuple(errors),
        )

    async def _retry_burst(
        self,
        *,
        spec: CoordinationPressureSpec,
        plan: Plan,
        steps: tuple[Step, ...],
        kernel: PlatformKernel,
        coordinator: DurablePlanStepCoordinator,
        projection: PlanCoordinationProjection,
        transition_samples: list[float],
        resume_samples: list[float],
        outcome_samples: list[float],
        observation_samples: list[float],
        retry_scheduled: set[str],
    ) -> tuple[DurablePlanStepCoordinator, PlanCoordinationProjection]:
        now = datetime.now(UTC)
        first_runs = _running_run_ids(projection)
        for step in steps:
            run_id = first_runs[step.id]
            outcome_started = time.perf_counter()
            await asyncio.wait_for(
                kernel.record_run_outcome(
                    idempotency_key=f"benchmark:{run_id}:failed",
                    task_id=plan.task_id,
                    run_id=run_id,
                    status=RunStatus.FAILED,
                ),
                timeout=spec.timeout_seconds,
            )
            outcome_samples.append(time.perf_counter() - outcome_started)

            transition_started = time.perf_counter()
            projection = await asyncio.wait_for(
                coordinator.observe_run(
                    task_id=plan.task_id,
                    run_id=run_id,
                    failure_category="benchmark-transient",
                    observation_key=f"benchmark:{run_id}:failed-observed",
                    now=now,
                ),
                timeout=spec.timeout_seconds,
            )
            transition_samples.append(time.perf_counter() - transition_started)
            item = _projection_step(projection, step.id)
            if item.phase is CoordinationPhase.RETRY_SCHEDULED:
                retry_scheduled.add(step.id)

        early = await asyncio.wait_for(
            coordinator.process_due(
                now=now + timedelta(seconds=max(0.0, spec.retry_delay_seconds - 0.001))
            ),
            timeout=spec.timeout_seconds,
        )
        if spec.retry_delay_seconds > 0 and early:
            raise RuntimeError("retry activated before its persisted due time")

        resume_started = time.perf_counter()
        await asyncio.wait_for(
            coordinator.process_due(now=now + timedelta(seconds=spec.retry_delay_seconds)),
            timeout=spec.timeout_seconds,
        )
        resume_samples.append(time.perf_counter() - resume_started)
        projection = coordinator.projection(plan.id)
        second_runs = _running_run_ids(projection)
        if len(second_runs) != spec.size:
            raise RuntimeError("retry wakeup did not reactivate every configured Step")
        if any(second_runs[step.id] == first_runs[step.id] for step in steps):
            raise RuntimeError("retry wakeup reused a canonical Run identity")

        projection = await _complete_running_steps(
            spec=spec,
            plan=plan,
            steps=steps,
            kernel=kernel,
            coordinator=coordinator,
            projection=projection,
            outcome_samples=outcome_samples,
            observation_samples=observation_samples,
        )
        return coordinator, projection

    async def _deadline_wait_burst(
        self,
        *,
        spec: CoordinationPressureSpec,
        plan: Plan,
        steps: tuple[Step, ...],
        kernel: PlatformKernel,
        coordinator: DurablePlanStepCoordinator,
        projection: PlanCoordinationProjection,
        transition_samples: list[float],
        resume_samples: list[float],
        outcome_samples: list[float],
        observation_samples: list[float],
        wait_entered: set[str],
        wait_resolved: set[str],
    ) -> tuple[DurablePlanStepCoordinator, PlanCoordinationProjection]:
        now = datetime.now(UTC)
        deadline = now + timedelta(seconds=spec.wait_delay_seconds)
        initial_runs = _running_run_ids(projection)
        for step in steps:
            transition_started = time.perf_counter()
            projection = await asyncio.wait_for(
                coordinator.wait_step(
                    StepWait(
                        wait_key=f"benchmark-wait:{step.id}",
                        wait_type=WaitType.DEADLINE,
                        task_id=plan.task_id,
                        plan_id=plan.id,
                        step_id=step.id,
                        owner_ref=step.owner_ref,
                        project_id=step.project_id,
                        deadline_at=deadline,
                    ),
                    now=now,
                ),
                timeout=spec.timeout_seconds,
            )
            transition_samples.append(time.perf_counter() - transition_started)
            item = _projection_step(projection, step.id)
            if item.status is StepStatus.WAITING and item.wait_type is WaitType.DEADLINE:
                wait_entered.add(step.id)

        before_due = await asyncio.wait_for(
            coordinator.process_due(
                now=deadline - timedelta(milliseconds=1)
                if spec.wait_delay_seconds > 0
                else deadline
            ),
            timeout=spec.timeout_seconds,
        )
        if spec.wait_delay_seconds > 0 and before_due:
            raise RuntimeError("deadline wait resumed before its persisted deadline")

        resume_started = time.perf_counter()
        await asyncio.wait_for(
            coordinator.process_due(now=deadline),
            timeout=spec.timeout_seconds,
        )
        resume_samples.append(time.perf_counter() - resume_started)
        projection = coordinator.projection(plan.id)
        resumed_runs = _running_run_ids(projection)
        for step in steps:
            item = _projection_step(projection, step.id)
            if item.status is StepStatus.RUNNING and item.wait_type is None:
                wait_resolved.add(step.id)
        if resumed_runs != initial_runs:
            raise RuntimeError("deadline wait wakeup changed canonical Run identities")

        projection = await _complete_running_steps(
            spec=spec,
            plan=plan,
            steps=steps,
            kernel=kernel,
            coordinator=coordinator,
            projection=projection,
            outcome_samples=outcome_samples,
            observation_samples=observation_samples,
        )
        return coordinator, projection

    async def _restart_reconcile(
        self,
        *,
        spec: CoordinationPressureSpec,
        plan: Plan,
        steps: tuple[Step, ...],
        orchestrator: _IndependentOrchestrator,
        lifecycle: FakeLifecycleBackend,
        kernel_path: Path,
        coordination_path: Path,
        initial_run_ids: dict[str, str],
        resume_samples: list[float],
        outcome_samples: list[float],
        observation_samples: list[float],
        reconciled_running: set[str],
    ) -> tuple[
        PlatformKernel,
        DurablePlanStepCoordinator,
        PlanCoordinationProjection,
        bool,
    ]:
        restarted_kernel = _kernel(kernel_path, orchestrator, lifecycle)
        restarted_coordinator = DurablePlanStepCoordinator(
            repository=SQLiteCoordinatorRepository(coordination_path),
            kernel=restarted_kernel,
            coordinator_id="benchmark-coordination-pressure-B",
        )
        reconcile_started = time.perf_counter()
        projection = await asyncio.wait_for(
            restarted_coordinator.reconcile_plan(plan.id),
            timeout=spec.timeout_seconds,
        )
        resume_samples.append(time.perf_counter() - reconcile_started)
        reconciled_runs = _running_run_ids(projection)
        identity_preserved = reconciled_runs == initial_run_ids
        for step in steps:
            item = _projection_step(projection, step.id)
            if item.status is StepStatus.RUNNING and item.latest_run_id == initial_run_ids[step.id]:
                reconciled_running.add(step.id)

        projection = await _complete_running_steps(
            spec=spec,
            plan=plan,
            steps=steps,
            kernel=restarted_kernel,
            coordinator=restarted_coordinator,
            projection=projection,
            outcome_samples=outcome_samples,
            observation_samples=observation_samples,
        )
        return restarted_kernel, restarted_coordinator, projection, identity_preserved


async def _complete_running_steps(
    *,
    spec: CoordinationPressureSpec,
    plan: Plan,
    steps: tuple[Step, ...],
    kernel: PlatformKernel,
    coordinator: DurablePlanStepCoordinator,
    projection: PlanCoordinationProjection,
    outcome_samples: list[float],
    observation_samples: list[float],
) -> PlanCoordinationProjection:
    running = _running_run_ids(projection)
    if len(running) != len(steps):
        raise RuntimeError("completion phase did not begin with every Step running")
    for step in steps:
        run_id = running[step.id]
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
                observation_key=f"benchmark:{run_id}:succeeded-observed",
            ),
            timeout=spec.timeout_seconds,
        )
        observation_samples.append(time.perf_counter() - observe_started)
    return projection


def _kernel(
    path: Path,
    orchestrator: _IndependentOrchestrator,
    lifecycle: FakeLifecycleBackend,
) -> PlatformKernel:
    return PlatformKernel(
        orchestrator=orchestrator,
        lifecycle=lifecycle,
        repository=SqliteKernelRepository(path),
    )


async def _plan_task(
    kernel: PlatformKernel,
    orchestrator: _IndependentOrchestrator,
    timeout_seconds: float,
) -> TaskState:
    created = await asyncio.wait_for(
        kernel.create_task(
            idempotency_key="benchmark:coordination-pressure:create",
            title="Coordination pressure benchmark",
            objective="measure deterministic retry wait and reconciliation overhead",
            owner_type="service",
            owner_id="benchmark-coordination-pressure",
            project_id=new_id("project"),
        ),
        timeout=timeout_seconds,
    )
    await asyncio.wait_for(
        kernel.ready_task(
            idempotency_key="benchmark:coordination-pressure:ready",
            task_id=created.task_id,
        ),
        timeout=timeout_seconds,
    )
    planned = await asyncio.wait_for(
        kernel.plan_task(
            idempotency_key="benchmark:coordination-pressure:plan",
            task_id=created.task_id,
        ),
        timeout=timeout_seconds,
    )
    if len(orchestrator.calls) != 1:
        raise RuntimeError("canonical planning seam was not invoked exactly once")
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
            project_id=planned.task.project_id,
        )
        for proposal in proposals
    )
    return plan, steps


def _projection_step(projection: PlanCoordinationProjection, step_id: str) -> Any:
    for item in projection.steps:
        if item.step_id == step_id:
            return item
    raise KeyError(step_id)


def _running_run_ids(projection: PlanCoordinationProjection) -> dict[str, str]:
    return {
        item.step_id: item.latest_run_id
        for item in projection.steps
        if item.status is StepStatus.RUNNING and item.latest_run_id is not None
    }


def _history_run_ids(history: tuple[Any, ...]) -> tuple[str, ...]:
    return tuple(
        event.subject_id
        for event in history
        if event.event_type == "run.created" and event.subject_type == "run"
    )


def _scenario_specific_passed(
    spec: CoordinationPressureSpec,
    *,
    retry_scheduled: set[str],
    wait_entered: set[str],
    wait_resolved: set[str],
    reconciled_running: set[str],
    run_identity_preserved: bool,
) -> bool:
    if spec.scenario == "retry-burst":
        return len(retry_scheduled) == spec.size
    if spec.scenario == "deadline-wait-burst":
        return len(wait_entered) == spec.size and len(wait_resolved) == spec.size
    return len(reconciled_running) == spec.size and run_identity_preserved


def _json_compatible(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    return value
