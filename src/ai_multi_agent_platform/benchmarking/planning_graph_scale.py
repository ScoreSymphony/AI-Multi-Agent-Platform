"""Deterministic large-Plan graph scale evidence for issue #440 after #439."""

from __future__ import annotations

import asyncio
import time
import tracemalloc
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.coordination import (
    DurablePlanStepCoordinator,
    SQLiteCoordinatorRepository,
)
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.planning import (
    AgentAssignment,
    DeterministicReferencePlanner,
    JsonPlanningRepository,
    PlanDraft,
    PlanningOrchestratorAdapter,
    PlanningService,
    PlanningStepDraft,
    ProposalStatus,
)
from ai_multi_agent_platform.planning.control_plane import PlanningProposalResourceService
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

from .models import LatencyDistribution, ResourceMetrics
from .single_node import (
    _directory_size,
    _environment_metadata,
    _open_file_descriptor_count,
    _peak_rss_bytes,
    _require_fresh_data_root,
)

PLANNING_GRAPH_SCALE_REPORT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class PlanningGraphScaleBenchmarkSpec:
    """One bounded deterministic sweep over canonical Plan graph sizes."""

    step_counts: tuple[int, ...] = (10, 100, 1000)
    repetitions: int = 3
    warmup_repetitions: int = 0
    timeout_seconds: float = 60.0
    safety_max_steps_per_plan: int = 2048
    safety_max_repetitions: int = 20
    benchmark_id: str = "single-node.planning-graph-scale"
    benchmark_version: str = "1.0"
    deployment_profile: str = "single-node-reference"
    persistence_profile: str = "sqlite-kernel+coordinator+json-planning"
    planner_profile: str = "deterministic-reference-no-model-provider"
    inspection_profile: str = "canonical-planning-proposal-resource"

    def __post_init__(self) -> None:
        if not self.step_counts:
            raise ValueError("step_counts must contain at least one graph size")
        if any(value < 1 for value in self.step_counts):
            raise ValueError("step_counts must contain only positive integers")
        if len(set(self.step_counts)) != len(self.step_counts):
            raise ValueError("step_counts must not contain duplicates")
        if tuple(sorted(self.step_counts)) != self.step_counts:
            raise ValueError("step_counts must be strictly increasing")
        if self.repetitions < 1:
            raise ValueError("repetitions must be at least 1")
        if self.warmup_repetitions < 0:
            raise ValueError("warmup_repetitions must not be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.safety_max_steps_per_plan < 1:
            raise ValueError("safety_max_steps_per_plan must be at least 1")
        if self.safety_max_repetitions < 1:
            raise ValueError("safety_max_repetitions must be at least 1")
        if max(self.step_counts) > self.safety_max_steps_per_plan:
            raise ValueError("step_counts exceed configured planning-graph safety bound")
        if self.repetitions > self.safety_max_repetitions:
            raise ValueError("repetitions exceed configured planning-graph safety bound")
        if self.warmup_repetitions > self.safety_max_repetitions:
            raise ValueError("warmup_repetitions exceed configured planning-graph safety bound")

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "step_counts": list(self.step_counts),
            "expected_invariants": [
                "every deterministic proposal validates at its declared graph-size bound",
                "every validated proposal activates through the canonical PlanningService",
                "activation hands the exact canonical Plan to the durable coordinator",
                "canonical planning-proposal inspection returns every generated Step",
                "deterministic reference planning performs no model/provider invocation",
            ],
            "captured_metrics": [
                "proposal+validation latency p50/p95/p99 per graph size",
                "activation+coordinator handoff latency p50/p95/p99 per graph size",
                "canonical proposal inspection latency p50/p95/p99 per graph size",
                "process CPU and traced memory across the sweep",
                "kernel/coordinator/planning storage growth across the sweep",
            ],
        }


@dataclass(frozen=True, slots=True)
class PlanningGraphScalePoint:
    step_count: int
    requested_repetitions: int
    completed_repetitions: int
    proposal_validation_latency: LatencyDistribution
    activation_handoff_latency: LatencyDistribution
    inspection_latency: LatencyDistribution
    validated_proposals: int
    activated_plans: int
    canonical_handoffs: int
    exact_step_inspections: int
    provider_invocations: int
    errors: tuple[str, ...]
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            {
                "step_count": self.step_count,
                "requested_repetitions": self.requested_repetitions,
                "completed_repetitions": self.completed_repetitions,
                "proposal_validation_latency": asdict(self.proposal_validation_latency),
                "activation_handoff_latency": asdict(self.activation_handoff_latency),
                "inspection_latency": asdict(self.inspection_latency),
                "validated_proposals": self.validated_proposals,
                "activated_plans": self.activated_plans,
                "canonical_handoffs": self.canonical_handoffs,
                "exact_step_inspections": self.exact_step_inspections,
                "provider_invocations": self.provider_invocations,
                "errors": list(self.errors),
                "passed": self.passed,
            },
        )


@dataclass(frozen=True, slots=True)
class PlanningGraphScaleCorrectnessSummary:
    requested_points: int
    completed_points: int
    requested_repetitions: int
    completed_repetitions: int
    provider_invocations: int
    passed: bool


@dataclass(frozen=True, slots=True)
class PlanningGraphScaleBenchmarkReport:
    schema_version: str
    benchmark: PlanningGraphScaleBenchmarkSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: dict[str, Any]
    points: tuple[PlanningGraphScalePoint, ...]
    resources: ResourceMetrics
    correctness: PlanningGraphScaleCorrectnessSummary
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            {
                "schema_version": self.schema_version,
                "benchmark": self.benchmark.to_dict(),
                "platform_version": self.platform_version,
                "platform_commit": self.platform_commit,
                "started_at": self.started_at,
                "duration_seconds": self.duration_seconds,
                "environment": self.environment,
                "points": [point.to_dict() for point in self.points],
                "resources": asdict(self.resources),
                "correctness": asdict(self.correctness),
                "errors": list(self.errors),
            },
        )


@dataclass(slots=True)
class _RepetitionResult:
    proposal_validation_seconds: float | None = None
    activation_handoff_seconds: float | None = None
    inspection_seconds: float | None = None
    validated: bool = False
    activated: bool = False
    canonical_handoff: bool = False
    exact_step_inspection: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _PlanningStack:
    planning: PlanningService
    resource_service: PlanningProposalResourceService
    kernel: PlatformKernel


class PlanningGraphScaleBenchmarkHarness:
    """Characterize graph-size overhead at canonical planning and inspection boundaries."""

    def __init__(self, data_dir: Path, *, platform_commit: str = "unknown") -> None:
        self._data_dir = data_dir
        self._platform_commit = platform_commit

    async def run(
        self,
        spec: PlanningGraphScaleBenchmarkSpec,
    ) -> PlanningGraphScaleBenchmarkReport:
        _require_fresh_data_root(self._data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        storage_before = _directory_size(self._data_dir)
        tracing_was_active = tracemalloc.is_tracing()
        if not tracing_was_active:
            tracemalloc.start()
        cpu_before = time.process_time()
        started_at = datetime.now(UTC).isoformat()
        wall_started = time.perf_counter()

        points: list[PlanningGraphScalePoint] = []
        for step_count in spec.step_counts:
            point = await self._run_point(spec, step_count)
            points.append(point)

        duration_seconds = max(time.perf_counter() - wall_started, 1e-9)
        cpu_seconds = max(time.process_time() - cpu_before, 0.0)
        traced_current, traced_peak = tracemalloc.get_traced_memory()
        if not tracing_was_active:
            tracemalloc.stop()
        storage_after = _directory_size(self._data_dir)

        errors = tuple(
            f"steps={point.step_count}: {error}"
            for point in points
            for error in point.errors
        )
        completed_points = sum(point.passed for point in points)
        requested_repetitions = spec.repetitions * len(points)
        completed_repetitions = sum(point.completed_repetitions for point in points)
        passed = not errors and completed_points == len(points)

        return PlanningGraphScaleBenchmarkReport(
            schema_version=PLANNING_GRAPH_SCALE_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at,
            duration_seconds=round(duration_seconds, 6),
            environment=_environment_metadata(),
            points=tuple(points),
            resources=ResourceMetrics(
                process_cpu_seconds=round(cpu_seconds, 6),
                traced_memory_current_bytes=traced_current,
                traced_memory_peak_bytes=traced_peak,
                peak_rss_bytes=_peak_rss_bytes(),
                storage_bytes_before=storage_before,
                storage_bytes_after=storage_after,
                storage_growth_bytes=storage_after - storage_before,
                open_file_descriptors=_open_file_descriptor_count(),
            ),
            correctness=PlanningGraphScaleCorrectnessSummary(
                requested_points=len(points),
                completed_points=completed_points,
                requested_repetitions=requested_repetitions,
                completed_repetitions=completed_repetitions,
                provider_invocations=0,
                passed=passed,
            ),
            errors=errors,
        )

    async def _run_point(
        self,
        spec: PlanningGraphScaleBenchmarkSpec,
        step_count: int,
    ) -> PlanningGraphScalePoint:
        stack = _build_stack(self._data_dir / f"steps-{step_count}", step_count)
        for index in range(spec.warmup_repetitions):
            warmup = await _exercise_repetition(
                stack,
                step_count=step_count,
                key=f"warmup-{index}",
                timeout_seconds=spec.timeout_seconds,
            )
            if warmup.error is not None:
                raise RuntimeError(
                    f"planning graph warmup failed at {step_count} Steps: {warmup.error}"
                )

        results = [
            await _exercise_repetition(
                stack,
                step_count=step_count,
                key=f"measured-{index}",
                timeout_seconds=spec.timeout_seconds,
            )
            for index in range(spec.repetitions)
        ]
        errors = tuple(error for result in results if (error := result.error) is not None)
        completed = sum(result.error is None for result in results)
        validated = sum(result.validated for result in results)
        activated = sum(result.activated for result in results)
        handoffs = sum(result.canonical_handoff for result in results)
        exact_inspections = sum(result.exact_step_inspection for result in results)
        passed = (
            not errors
            and completed == spec.repetitions
            and validated == spec.repetitions
            and activated == spec.repetitions
            and handoffs == spec.repetitions
            and exact_inspections == spec.repetitions
        )
        return PlanningGraphScalePoint(
            step_count=step_count,
            requested_repetitions=spec.repetitions,
            completed_repetitions=completed,
            proposal_validation_latency=LatencyDistribution.from_seconds(
                _samples(results, "proposal_validation_seconds")
            ),
            activation_handoff_latency=LatencyDistribution.from_seconds(
                _samples(results, "activation_handoff_seconds")
            ),
            inspection_latency=LatencyDistribution.from_seconds(
                _samples(results, "inspection_seconds")
            ),
            validated_proposals=validated,
            activated_plans=activated,
            canonical_handoffs=handoffs,
            exact_step_inspections=exact_inspections,
            provider_invocations=0,
            errors=errors,
            passed=passed,
        )


def _build_stack(data_dir: Path, step_count: int) -> _PlanningStack:
    data_dir.mkdir(parents=True, exist_ok=False)
    db_dir = data_dir / "db"
    db_dir.mkdir()
    owner = OwnerRef(type="service", id="benchmark-planning-graph-scale")
    agents = InMemoryAgentRepository()
    revision = AgentService(agents).create_agent(
        AgentProfile(
            name="Planning graph scale worker",
            role="worker",
            instructions=AgentInstructions(
                role=InstructionSource(
                    content="Execute deterministic large-graph benchmark Steps.",
                    version="1",
                )
            ),
        ),
        owner_ref=owner,
    )
    repository = JsonPlanningRepository(data_dir / "planning.json")
    kernel = PlatformKernel(
        orchestrator=PlanningOrchestratorAdapter(repository, fallback=FakeOrchestrator()),
        lifecycle=FakeLifecycleBackend(),
        repository=SqliteKernelRepository(db_dir / "kernel.sqlite3"),
    )
    coordinator = DurablePlanStepCoordinator(
        repository=SQLiteCoordinatorRepository(db_dir / "coordination.sqlite3"),
        kernel=kernel,
        coordinator_id=f"benchmark-planning-graph-{step_count}",
    )
    planning = PlanningService(
        planner=DeterministicReferencePlanner(
            _scale_draft(revision.agent_id, revision.revision, step_count)
        ),
        repository=repository,
        kernel=kernel,
        agents=agents,
        coordinator=coordinator,
    )
    return _PlanningStack(
        planning=planning,
        resource_service=PlanningProposalResourceService(planning),
        kernel=kernel,
    )


async def _exercise_repetition(
    stack: _PlanningStack,
    *,
    step_count: int,
    key: str,
    timeout_seconds: float,
) -> _RepetitionResult:
    result = _RepetitionResult()
    try:
        task = await asyncio.wait_for(
            stack.kernel.create_task(
                idempotency_key=f"planning-graph:{step_count}:{key}:create",
                title=f"Planning graph scale {step_count}",
                objective="Measure deterministic canonical planning graph-size overhead",
                owner_type="service",
                owner_id="benchmark-planning-graph-scale",
            ),
            timeout=timeout_seconds,
        )
        ready = await asyncio.wait_for(
            stack.kernel.ready_task(
                idempotency_key=f"planning-graph:{step_count}:{key}:ready",
                task_id=task.task_id,
            ),
            timeout=timeout_seconds,
        )

        started = time.perf_counter()
        proposal = await asyncio.wait_for(
            stack.planning.propose(
                task_id=ready.task_id,
                idempotency_key=f"planning-graph:{step_count}:{key}:propose",
                max_steps=step_count,
            ),
            timeout=timeout_seconds,
        )
        result.proposal_validation_seconds = time.perf_counter() - started
        result.validated = (
            proposal.status is ProposalStatus.VALIDATED
            and proposal.validation.valid
            and len(proposal.proposal.steps) == step_count
        )

        started = time.perf_counter()
        activated = await asyncio.wait_for(
            stack.planning.activate(
                proposal.proposal.proposal_id,
                idempotency_key=f"planning-graph:{step_count}:{key}:activate",
            ),
            timeout=timeout_seconds,
        )
        result.activation_handoff_seconds = time.perf_counter() - started
        result.activated = (
            activated.status is ProposalStatus.ACTIVATED
            and activated.activation_plan_id is not None
        )
        task_after_activation = await stack.kernel.get_task(ready.task_id)
        result.canonical_handoff = (
            activated.activation_plan_id is not None
            and task_after_activation.plan_ref == activated.activation_plan_id
            and bool(task_after_activation.run_ids)
        )

        context = RequestContext(
            request_id=f"planning-graph:{step_count}:{key}:inspect",
            correlation_id=ready.task_id,
        )
        started = time.perf_counter()
        projected = await asyncio.wait_for(
            stack.resource_service.get_resource(context, proposal.proposal.proposal_id),
            timeout=timeout_seconds,
        )
        result.inspection_seconds = time.perf_counter() - started
        projected_steps = projected.get("steps")
        result.exact_step_inspection = (
            isinstance(projected_steps, list)
            and len(projected_steps) == step_count
            and projected.get("activation_plan_id") == activated.activation_plan_id
        )
    except Exception as exc:  # benchmark evidence must preserve correctness failures
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def _scale_draft(agent_id: str, revision: int, count: int) -> PlanDraft:
    """Create a shallow fanout DAG so graph volume, not Python recursion depth, is the variable."""

    steps: list[PlanningStepDraft] = []
    root_key = "step-1"
    for index in range(count):
        key = f"step-{index + 1}"
        depends_on = () if index == 0 else (root_key,)
        steps.append(
            PlanningStepDraft(
                key=key,
                title=f"Deterministic graph Step {index + 1}",
                objective="Exercise large canonical Plan validation, activation and inspection",
                depends_on=depends_on,
                assignment=AgentAssignment(agent_id=agent_id, agent_revision=revision),
            )
        )
    return PlanDraft(
        summary=f"Deterministic {count}-Step planning graph scale Plan",
        steps=tuple(steps),
    )


def _samples(results: list[_RepetitionResult], field: str) -> list[float]:
    samples: list[float] = []
    for result in results:
        value = getattr(result, field)
        if isinstance(value, float):
            samples.append(value)
    return samples


__all__ = [
    "PLANNING_GRAPH_SCALE_REPORT_SCHEMA_VERSION",
    "PlanningGraphScaleBenchmarkHarness",
    "PlanningGraphScaleBenchmarkReport",
    "PlanningGraphScaleBenchmarkSpec",
    "PlanningGraphScaleCorrectnessSummary",
    "PlanningGraphScalePoint",
]
