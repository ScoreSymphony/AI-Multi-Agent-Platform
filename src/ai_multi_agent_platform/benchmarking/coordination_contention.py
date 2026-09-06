"""Concurrent multi-Plan and coordinator claim-contention evidence for issue #440."""

from __future__ import annotations

import asyncio
import time
import tracemalloc
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.contracts import (
    ContractError,
    PlanRequest,
    PlanResponse,
    PlanStepProposal,
)
from ai_multi_agent_platform.coordination import (
    CoordinatorClaim,
    DurablePlanStepCoordinator,
    PlanCoordinationProjection,
    SQLiteCoordinatorRepository,
    StepCoordinationProjection,
)
from ai_multi_agent_platform.domain import (
    Event,
    Plan,
    RunStatus,
    Step,
    StepStatus,
    TaskStatus,
    new_id,
)
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

COORDINATION_CONTENTION_REPORT_SCHEMA_VERSION = "1.0"
CoordinationContentionScenario = Literal["multi-plan", "claim-contention"]
_COORDINATOR_COUNT = 2


@dataclass(frozen=True, slots=True)
class CoordinationContentionSpec:
    """One bounded concurrent durable-coordination workload."""

    scenario: CoordinationContentionScenario
    plan_count: int
    steps_per_plan: int
    claim_hold_seconds: float = 1.0
    timeout_seconds: float = 30.0
    safety_max_total_steps: int = 2048
    benchmark_id: str = "single-node.coordination-contention"
    benchmark_version: str = "1.0"
    deployment_profile: str = "single-node-reference"
    persistence_profile: str = "sqlite-kernel+coordinator"
    coordinator_count: int = _COORDINATOR_COUNT

    def __post_init__(self) -> None:
        if self.scenario not in {"multi-plan", "claim-contention"}:
            raise ValueError(f"unsupported coordination contention scenario: {self.scenario}")
        if self.plan_count < 1:
            raise ValueError("plan_count must be at least 1")
        if self.steps_per_plan < 1:
            raise ValueError("steps_per_plan must be at least 1")
        if self.safety_max_total_steps < 1:
            raise ValueError("safety_max_total_steps must be at least 1")
        if self.total_steps > self.safety_max_total_steps:
            raise ValueError("workload exceeds configured coordination contention safety bound")
        if self.claim_hold_seconds <= 0:
            raise ValueError("claim_hold_seconds must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.coordinator_count != _COORDINATOR_COUNT:
            raise ValueError("coordination contention v1 requires exactly two coordinators")

    @property
    def total_steps(self) -> int:
        return self.plan_count * self.steps_per_plan

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "total_steps": self.total_steps,
            "expected_invariants": [
                "every canonical Plan and Step reaches succeeded",
                "exactly one canonical Run is created per Step",
                "competing coordinators never duplicate Run identities",
                "foreign unexpired claims block the competing coordinator",
                "expired claims are fenced out and recovery advances the fence",
            ],
            "captured_metrics": [
                "Plan registration latency p50/p95/p99",
                "kernel run-outcome persistence latency p50/p95/p99",
                "blocked claim observation latency p50/p95/p99",
                "successful completion observation latency p50/p95/p99",
                "completed Steps per second",
                "process CPU and traced memory",
                "SQLite storage growth",
            ],
        }


@dataclass(frozen=True, slots=True)
class CoordinationContentionCorrectnessSummary:
    expected_plans: int
    succeeded_tasks: int
    expected_steps: int
    succeeded_steps: int
    expected_runs: int
    run_created_events: int
    unique_run_ids: int
    blocked_observations: int
    recovered_observations: int
    stale_claim_rejections: int
    fence_advanced_steps: int
    passed: bool


@dataclass(frozen=True, slots=True)
class CoordinationContentionReport:
    schema_version: str
    benchmark: CoordinationContentionSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: dict[str, Any]
    throughput_steps_per_second: float
    registration_latency: LatencyDistribution
    outcome_persistence_latency: LatencyDistribution
    blocked_observation_latency: LatencyDistribution
    completion_observation_latency: LatencyDistribution
    resources: ResourceMetrics
    correctness: CoordinationContentionCorrectnessSummary
    task_ids: tuple[str, ...]
    plan_ids: tuple[str, ...]
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
                    "blocked_observation_latency": asdict(self.blocked_observation_latency),
                    "completion_observation_latency": asdict(self.completion_observation_latency),
                    "resources": asdict(self.resources),
                    "correctness": asdict(self.correctness),
                    "task_ids": self.task_ids,
                    "plan_ids": self.plan_ids,
                    "step_ids": self.step_ids,
                    "run_ids": self.run_ids,
                    "errors": self.errors,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class _Workflow:
    plan: Plan
    steps: tuple[Step, ...]


class _IndependentPlanOrchestrator(FakeOrchestrator):
    """Return the same deterministic independent-Step graph for every planned Task."""

    def __init__(self, steps_per_plan: int) -> None:
        super().__init__(summary_prefix="Deterministic multi-Plan benchmark")
        self.proposals = tuple(
            PlanStepProposal(
                key=f"step-{index}",
                title=f"Concurrent Step {index}",
                objective="exercise concurrent durable coordination",
            )
            for index in range(steps_per_plan)
        )

    async def plan(self, request: PlanRequest) -> PlanResponse:
        self.calls.append(request)
        return PlanResponse(
            summary=f"Deterministic {len(self.proposals)}-Step {request.objective}",
            steps=self.proposals,
        )


class CoordinationContentionHarness:
    """Measure concurrent Plans and fenced claim takeover on canonical coordination paths."""

    def __init__(self, data_dir: Path, *, platform_commit: str = "unknown") -> None:
        self._data_dir = data_dir
        self._platform_commit = platform_commit

    async def run(self, spec: CoordinationContentionSpec) -> CoordinationContentionReport:
        _require_fresh_data_root(self._data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        db_dir = self._data_dir / "db"
        db_dir.mkdir(parents=True, exist_ok=True)

        orchestrator = _IndependentPlanOrchestrator(spec.steps_per_plan)
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=orchestrator,
            lifecycle=lifecycle,
            repository=SqliteKernelRepository(db_dir / "kernel.sqlite3"),
        )
        workflows = await _plan_workflows(kernel, orchestrator, spec)
        if len(orchestrator.calls) != spec.plan_count:
            raise RuntimeError("canonical planner invocation count differs from plan_count")

        coordination_path = db_dir / "coordination.sqlite3"
        repositories = tuple(
            SQLiteCoordinatorRepository(coordination_path) for _ in range(_COORDINATOR_COUNT)
        )
        coordinators = tuple(
            DurablePlanStepCoordinator(
                repository=repositories[index],
                kernel=kernel,
                coordinator_id=f"benchmark-contention-{index}",
                claim_ttl=timedelta(seconds=spec.claim_hold_seconds),
            )
            for index in range(_COORDINATOR_COUNT)
        )

        storage_before = _directory_size(self._data_dir)
        tracing_was_active = tracemalloc.is_tracing()
        if not tracing_was_active:
            tracemalloc.start()
        cpu_before = time.process_time()
        wall_started = time.perf_counter()
        started_at = datetime.now(UTC).isoformat()

        registration_samples: list[float] = []
        outcome_samples: list[float] = []
        blocked_samples: list[float] = []
        completion_samples: list[float] = []
        blocked_observations = 0
        recovered_observations = 0
        stale_claim_rejections = 0
        fence_advanced_steps = 0
        errors: list[str] = []

        try:
            projections = await self._register_workflows(
                spec=spec,
                workflows=workflows,
                coordinators=coordinators,
                registration_samples=registration_samples,
            )
            active_runs = _active_runs(workflows, projections)
            if len(active_runs) != spec.total_steps:
                raise RuntimeError("registration did not create one active Run per Step")

            await _persist_outcomes(
                spec=spec,
                kernel=kernel,
                active_runs=active_runs,
                outcome_samples=outcome_samples,
            )

            if spec.scenario == "multi-plan":
                await _observe_completions(
                    spec=spec,
                    coordinators=coordinators,
                    active_runs=active_runs,
                    completion_samples=completion_samples,
                )
            else:
                (
                    blocked_observations,
                    recovered_observations,
                    stale_claim_rejections,
                    fence_advanced_steps,
                ) = await self._exercise_claim_contention(
                    spec=spec,
                    workflows=workflows,
                    repositories=repositories,
                    coordinators=coordinators,
                    active_runs=active_runs,
                    blocked_samples=blocked_samples,
                    completion_samples=completion_samples,
                )
        except Exception as exc:  # benchmark evidence retains deterministic failures
            errors.append(f"{type(exc).__name__}: {exc}")

        duration = time.perf_counter() - wall_started
        process_cpu_seconds = time.process_time() - cpu_before
        traced_current, traced_peak = tracemalloc.get_traced_memory()
        if not tracing_was_active:
            tracemalloc.stop()
        storage_after = _directory_size(self._data_dir)

        projections = _available_projections(workflows, coordinators[0])
        succeeded_steps = sum(
            item.status is StepStatus.SUCCEEDED
            for projection in projections
            for item in projection.steps
        )
        task_states: tuple[TaskState, ...] = tuple(
            await asyncio.gather(
                *(kernel.get_task(workflow.plan.task_id) for workflow in workflows)
            )
        )
        succeeded_tasks = sum(state.status is TaskStatus.SUCCEEDED for state in task_states)
        histories: tuple[tuple[Event, ...], ...] = tuple(
            await asyncio.gather(
                *(kernel.history(workflow.plan.task_id) for workflow in workflows)
            )
        )
        run_ids = tuple(run_id for history in histories for run_id in _history_run_ids(history))
        run_created_events = len(run_ids)
        expected_contention = spec.total_steps if spec.scenario == "claim-contention" else 0
        correctness = CoordinationContentionCorrectnessSummary(
            expected_plans=spec.plan_count,
            succeeded_tasks=succeeded_tasks,
            expected_steps=spec.total_steps,
            succeeded_steps=succeeded_steps,
            expected_runs=spec.total_steps,
            run_created_events=run_created_events,
            unique_run_ids=len(set(run_ids)),
            blocked_observations=blocked_observations,
            recovered_observations=recovered_observations,
            stale_claim_rejections=stale_claim_rejections,
            fence_advanced_steps=fence_advanced_steps,
            passed=(
                not errors
                and len(projections) == spec.plan_count
                and succeeded_tasks == spec.plan_count
                and succeeded_steps == spec.total_steps
                and run_created_events == spec.total_steps
                and len(set(run_ids)) == spec.total_steps
                and blocked_observations == expected_contention
                and recovered_observations == expected_contention
                and stale_claim_rejections == expected_contention
                and fence_advanced_steps == expected_contention
            ),
        )
        throughput = succeeded_steps / duration if duration > 0 else 0.0
        return CoordinationContentionReport(
            schema_version=COORDINATION_CONTENTION_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at,
            duration_seconds=round(duration, 6),
            environment=_environment_metadata(),
            throughput_steps_per_second=round(throughput, 6),
            registration_latency=LatencyDistribution.from_seconds(registration_samples),
            outcome_persistence_latency=LatencyDistribution.from_seconds(outcome_samples),
            blocked_observation_latency=LatencyDistribution.from_seconds(blocked_samples),
            completion_observation_latency=LatencyDistribution.from_seconds(completion_samples),
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
            task_ids=tuple(workflow.plan.task_id for workflow in workflows),
            plan_ids=tuple(workflow.plan.id for workflow in workflows),
            step_ids=tuple(step.id for workflow in workflows for step in workflow.steps),
            run_ids=run_ids,
            errors=tuple(errors),
        )

    async def _register_workflows(
        self,
        *,
        spec: CoordinationContentionSpec,
        workflows: tuple[_Workflow, ...],
        coordinators: tuple[DurablePlanStepCoordinator, ...],
        registration_samples: list[float],
    ) -> tuple[PlanCoordinationProjection, ...]:
        async def register(index: int, workflow: _Workflow) -> PlanCoordinationProjection:
            started = time.perf_counter()
            projection = await asyncio.wait_for(
                coordinators[index % len(coordinators)].register_plan(
                    workflow.plan,
                    workflow.steps,
                ),
                timeout=spec.timeout_seconds,
            )
            registration_samples.append(time.perf_counter() - started)
            return projection

        return tuple(
            await asyncio.gather(
                *(register(index, workflow) for index, workflow in enumerate(workflows))
            )
        )

    async def _exercise_claim_contention(
        self,
        *,
        spec: CoordinationContentionSpec,
        workflows: tuple[_Workflow, ...],
        repositories: tuple[SQLiteCoordinatorRepository, ...],
        coordinators: tuple[DurablePlanStepCoordinator, ...],
        active_runs: dict[str, tuple[str, str]],
        blocked_samples: list[float],
        completion_samples: list[float],
    ) -> tuple[int, int, int, int]:
        holder = repositories[0]
        contender = coordinators[1]
        t0 = datetime.now(UTC)
        ttl = timedelta(seconds=spec.claim_hold_seconds)
        claims: dict[str, CoordinatorClaim] = {}
        for workflow in workflows:
            for step in workflow.steps:
                claim = holder.acquire_claim(
                    step_id=step.id,
                    owner_id=coordinators[0].coordinator_id,
                    ttl=ttl,
                    now=t0,
                )
                if claim is None:
                    raise RuntimeError(f"fixture could not acquire initial claim for {step.id}")
                claims[step.id] = claim

        blocked_now = t0 + ttl / 2

        async def blocked_observe(step_id: str, task_id: str, run_id: str) -> int:
            started = time.perf_counter()
            projection = await asyncio.wait_for(
                contender.observe_run(
                    task_id=task_id,
                    run_id=run_id,
                    observation_key=f"benchmark:{run_id}:contention",
                    now=blocked_now,
                ),
                timeout=spec.timeout_seconds,
            )
            blocked_samples.append(time.perf_counter() - started)
            item = _projection_step(projection, step_id)
            return int(item.status is StepStatus.RUNNING)

        blocked = sum(
            await asyncio.gather(
                *(
                    blocked_observe(step_id, task_id, run_id)
                    for step_id, (task_id, run_id) in active_runs.items()
                )
            )
        )

        recovery_now = t0 + ttl

        async def recover(step_id: str, task_id: str, run_id: str) -> int:
            started = time.perf_counter()
            projection = await asyncio.wait_for(
                contender.observe_run(
                    task_id=task_id,
                    run_id=run_id,
                    observation_key=f"benchmark:{run_id}:contention",
                    now=recovery_now,
                ),
                timeout=spec.timeout_seconds,
            )
            completion_samples.append(time.perf_counter() - started)
            return int(_projection_step(projection, step_id).status is StepStatus.SUCCEEDED)

        recovered = sum(
            await asyncio.gather(
                *(
                    recover(step_id, task_id, run_id)
                    for step_id, (task_id, run_id) in active_runs.items()
                )
            )
        )

        stale_rejections = 0
        advanced_fences = 0
        audit_now = recovery_now + timedelta(milliseconds=1)
        for step_id, stale in claims.items():
            renewed = holder.renew_claim(claim=stale, ttl=ttl, now=audit_now)
            stale_release = holder.release_claim(stale)
            if renewed is None and not stale_release:
                stale_rejections += 1
            audit = holder.acquire_claim(
                step_id=step_id,
                owner_id="benchmark-contention-audit",
                ttl=ttl,
                now=audit_now,
            )
            if audit is None:
                raise RuntimeError(f"could not acquire post-recovery audit claim for {step_id}")
            if audit.fence > stale.fence:
                advanced_fences += 1
            holder.release_claim(audit)
        return blocked, recovered, stale_rejections, advanced_fences


async def _plan_workflows(
    kernel: PlatformKernel,
    orchestrator: _IndependentPlanOrchestrator,
    spec: CoordinationContentionSpec,
) -> tuple[_Workflow, ...]:
    workflows: list[_Workflow] = []
    for index in range(spec.plan_count):
        created = await asyncio.wait_for(
            kernel.create_task(
                idempotency_key=f"benchmark:contention:{index}:create",
                title=f"Coordination contention Task {index}",
                objective="measure concurrent multi-Plan coordination",
                owner_type="service",
                owner_id="benchmark-coordination-contention",
                project_id=new_id("project"),
            ),
            timeout=spec.timeout_seconds,
        )
        await asyncio.wait_for(
            kernel.ready_task(
                idempotency_key=f"benchmark:contention:{index}:ready",
                task_id=created.task_id,
            ),
            timeout=spec.timeout_seconds,
        )
        planned = await asyncio.wait_for(
            kernel.plan_task(
                idempotency_key=f"benchmark:contention:{index}:plan",
                task_id=created.task_id,
            ),
            timeout=spec.timeout_seconds,
        )
        workflows.append(_materialize_workflow(planned, orchestrator.proposals))
    return tuple(workflows)


def _materialize_workflow(
    planned: TaskState,
    proposals: tuple[PlanStepProposal, ...],
) -> _Workflow:
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
    return _Workflow(
        plan=plan,
        steps=tuple(
            Step(
                id=ids_by_key[proposal.key],
                plan_id=plan.id,
                title=proposal.title,
                owner_ref=planned.task.owner_ref,
                project_id=planned.task.project_id,
            )
            for proposal in proposals
        ),
    )


def _active_runs(
    workflows: tuple[_Workflow, ...],
    projections: tuple[PlanCoordinationProjection, ...],
) -> dict[str, tuple[str, str]]:
    tasks_by_plan = {workflow.plan.id: workflow.plan.task_id for workflow in workflows}
    active: dict[str, tuple[str, str]] = {}
    for projection in projections:
        task_id = tasks_by_plan[projection.plan_id]
        for item in projection.steps:
            if item.status is StepStatus.RUNNING and item.latest_run_id is not None:
                active[item.step_id] = (task_id, item.latest_run_id)
    return active


async def _persist_outcomes(
    *,
    spec: CoordinationContentionSpec,
    kernel: PlatformKernel,
    active_runs: dict[str, tuple[str, str]],
    outcome_samples: list[float],
) -> None:
    async def persist(task_id: str, run_id: str) -> None:
        started = time.perf_counter()
        await asyncio.wait_for(
            kernel.record_run_outcome(
                idempotency_key=f"benchmark:{run_id}:succeeded",
                task_id=task_id,
                run_id=run_id,
                status=RunStatus.SUCCEEDED,
            ),
            timeout=spec.timeout_seconds,
        )
        outcome_samples.append(time.perf_counter() - started)

    await asyncio.gather(*(persist(task_id, run_id) for task_id, run_id in active_runs.values()))


async def _observe_completions(
    *,
    spec: CoordinationContentionSpec,
    coordinators: tuple[DurablePlanStepCoordinator, ...],
    active_runs: dict[str, tuple[str, str]],
    completion_samples: list[float],
) -> None:
    async def observe(index: int, task_id: str, run_id: str) -> None:
        started = time.perf_counter()
        await asyncio.wait_for(
            coordinators[index % len(coordinators)].observe_run(
                task_id=task_id,
                run_id=run_id,
                observation_key=f"benchmark:{run_id}:completed",
            ),
            timeout=spec.timeout_seconds,
        )
        completion_samples.append(time.perf_counter() - started)

    await asyncio.gather(
        *(
            observe(index, task_id, run_id)
            for index, (_, (task_id, run_id)) in enumerate(active_runs.items())
        )
    )


def _available_projections(
    workflows: tuple[_Workflow, ...],
    coordinator: DurablePlanStepCoordinator,
) -> tuple[PlanCoordinationProjection, ...]:
    projections: list[PlanCoordinationProjection] = []
    for workflow in workflows:
        try:
            projections.append(coordinator.projection(workflow.plan.id))
        except ContractError:
            continue
    return tuple(projections)


def _projection_step(
    projection: PlanCoordinationProjection,
    step_id: str,
) -> StepCoordinationProjection:
    for item in projection.steps:
        if item.step_id == step_id:
            return item
    raise KeyError(step_id)


def _history_run_ids(history: tuple[Event, ...]) -> tuple[str, ...]:
    return tuple(
        event.subject_id
        for event in history
        if event.event_type == "run.created" and event.subject_type == "run"
    )


def _json_compatible(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    return value
