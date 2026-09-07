"""Deterministic autonomous-planning pressure evidence for issue #440 after #439."""

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
from ai_multi_agent_platform.contracts import ExecutionStatus
from ai_multi_agent_platform.coordination import (
    DurablePlanStepCoordinator,
    SQLiteCoordinatorRepository,
)
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
    PlanningTrigger,
    ReplanningEvidenceBridge,
    ReplanPolicy,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

from .models import LatencyDistribution, ResourceMetrics
from .single_node import (
    _directory_size,
    _environment_metadata,
    _open_file_descriptor_count,
    _peak_rss_bytes,
    _require_fresh_data_root,
)

PLANNING_PRESSURE_REPORT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class PlanningPressureBenchmarkSpec:
    """One bounded deterministic proposal/activation/replanning profile."""

    operation_count: int
    concurrency: int
    steps_per_plan: int = 3
    warmup_operations: int = 0
    timeout_seconds: float = 30.0
    safety_max_operations: int = 500
    safety_max_concurrency: int = 64
    safety_max_steps_per_plan: int = 128
    benchmark_id: str = "single-node.planning-pressure"
    benchmark_version: str = "1.0"
    deployment_profile: str = "single-node-reference"
    persistence_profile: str = "sqlite-kernel+coordinator+json-planning"
    planner_profile: str = "deterministic-reference-no-model-provider"

    def __post_init__(self) -> None:
        if self.operation_count < 1:
            raise ValueError("operation_count must be at least 1")
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if self.steps_per_plan < 1:
            raise ValueError("steps_per_plan must be at least 1")
        if self.warmup_operations < 0:
            raise ValueError("warmup_operations must not be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.operation_count > self.safety_max_operations:
            raise ValueError(
                "operation_count exceeds configured planning-pressure safety bound"
            )
        if self.concurrency > self.safety_max_concurrency:
            raise ValueError("concurrency exceeds configured planning-pressure safety bound")
        if self.steps_per_plan > self.safety_max_steps_per_plan:
            raise ValueError("steps_per_plan exceeds configured planning-pressure safety bound")

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "expected_invariants": [
                "every measured initial proposal validates and activates",
                "every replan is derived from the current canonical failed Run",
                "every replacement proposal validates and activates a new canonical Plan",
                "deterministic reference planner performs no model/provider invocation",
                "planning and execution evidence remain separate benchmark concerns",
            ],
            "captured_metrics": [
                "initial proposal+validation latency p50/p95/p99",
                "initial activation+coordinator handoff latency p50/p95/p99",
                "terminal-evidence replanning proposal latency p50/p95/p99",
                "replacement activation+coordinator handoff latency p50/p95/p99",
                "completed planning lifecycles per second",
                "process CPU and traced memory",
                "kernel/coordinator/planning storage growth",
            ],
        }


@dataclass(frozen=True, slots=True)
class PlanningPressureCorrectnessSummary:
    requested_cycles: int
    completed_cycles: int
    initial_proposals_validated: int
    initial_plans_activated: int
    terminal_replans_validated: int
    replacement_plans_activated: int
    canonical_failure_evidence_resolved: int
    distinct_replacement_plans: int
    provider_invocations: int
    passed: bool


@dataclass(frozen=True, slots=True)
class PlanningPressureBenchmarkReport:
    schema_version: str
    benchmark: PlanningPressureBenchmarkSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: dict[str, Any]
    throughput_lifecycles_per_second: float
    initial_proposal_latency: LatencyDistribution
    initial_activation_latency: LatencyDistribution
    replan_proposal_latency: LatencyDistribution
    replan_activation_latency: LatencyDistribution
    resources: ResourceMetrics
    correctness: PlanningPressureCorrectnessSummary
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
                "throughput_lifecycles_per_second": self.throughput_lifecycles_per_second,
                "initial_proposal_latency": asdict(self.initial_proposal_latency),
                "initial_activation_latency": asdict(self.initial_activation_latency),
                "replan_proposal_latency": asdict(self.replan_proposal_latency),
                "replan_activation_latency": asdict(self.replan_activation_latency),
                "resources": asdict(self.resources),
                "correctness": asdict(self.correctness),
                "errors": list(self.errors),
            },
        )


@dataclass(slots=True)
class _CycleResult:
    initial_proposal_seconds: float | None = None
    initial_activation_seconds: float | None = None
    replan_proposal_seconds: float | None = None
    replan_activation_seconds: float | None = None
    initial_validated: bool = False
    initial_activated: bool = False
    replan_validated: bool = False
    replan_activated: bool = False
    canonical_failure_evidence_resolved: bool = False
    distinct_replacement_plan: bool = False
    error: str | None = None


class PlanningPressureBenchmarkHarness:
    """Measure platform-owned planning lifecycle overhead without a model provider."""

    def __init__(self, data_dir: Path, *, platform_commit: str = "unknown") -> None:
        self._data_dir = data_dir
        self._platform_commit = platform_commit

    async def run(self, spec: PlanningPressureBenchmarkSpec) -> PlanningPressureBenchmarkReport:
        _require_fresh_data_root(self._data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        db_dir = self._data_dir / "db"
        db_dir.mkdir(parents=True, exist_ok=True)

        owner = OwnerRef(type="service", id="benchmark-planning-pressure")
        agents = InMemoryAgentRepository()
        agent_revision = AgentService(agents).create_agent(
            AgentProfile(
                name="Planning pressure worker",
                role="worker",
                instructions=AgentInstructions(
                    role=InstructionSource(
                        content="Execute deterministic benchmark Steps.",
                        version="1",
                    )
                ),
            ),
            owner_ref=owner,
        )
        draft = _linear_draft(
            agent_revision.agent_id,
            agent_revision.revision,
            spec.steps_per_plan,
        )
        planning_repository = JsonPlanningRepository(self._data_dir / "planning.json")
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=PlanningOrchestratorAdapter(
                planning_repository,
                fallback=FakeOrchestrator(),
            ),
            lifecycle=lifecycle,
            repository=SqliteKernelRepository(db_dir / "kernel.sqlite3"),
        )
        coordinator = DurablePlanStepCoordinator(
            repository=SQLiteCoordinatorRepository(db_dir / "coordination.sqlite3"),
            kernel=kernel,
            coordinator_id="benchmark-planning-pressure",
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(draft),
            repository=planning_repository,
            kernel=kernel,
            agents=agents,
            coordinator=coordinator,
            replan_policy=ReplanPolicy(max_replans=1),
        )
        bridge = ReplanningEvidenceBridge(planning)

        if spec.warmup_operations:
            warmup_tasks = await _ready_tasks(
                kernel,
                count=spec.warmup_operations,
                prefix="warmup",
            )
            for index, task_id in enumerate(warmup_tasks):
                result = await _exercise_cycle(
                    planning=planning,
                    bridge=bridge,
                    coordinator=coordinator,
                    lifecycle=lifecycle,
                    kernel=kernel,
                    task_id=task_id,
                    key=f"warmup-{index}",
                    timeout_seconds=spec.timeout_seconds,
                )
                if result.error is not None:
                    raise RuntimeError(f"planning-pressure warmup failed: {result.error}")

        measured_tasks = await _ready_tasks(
            kernel,
            count=spec.operation_count,
            prefix="measured",
        )
        storage_before = _directory_size(self._data_dir)
        tracing_was_active = tracemalloc.is_tracing()
        if not tracing_was_active:
            tracemalloc.start()
        cpu_before = time.process_time()
        started_at = datetime.now(UTC).isoformat()
        wall_started = time.perf_counter()

        semaphore = asyncio.Semaphore(spec.concurrency)

        async def guarded(index: int, task_id: str) -> _CycleResult:
            async with semaphore:
                return await _exercise_cycle(
                    planning=planning,
                    bridge=bridge,
                    coordinator=coordinator,
                    lifecycle=lifecycle,
                    kernel=kernel,
                    task_id=task_id,
                    key=f"measured-{index}",
                    timeout_seconds=spec.timeout_seconds,
                )

        results = await asyncio.gather(
            *(guarded(index, task_id) for index, task_id in enumerate(measured_tasks))
        )
        duration_seconds = max(time.perf_counter() - wall_started, 1e-9)
        cpu_seconds = max(time.process_time() - cpu_before, 0.0)
        traced_current, traced_peak = tracemalloc.get_traced_memory()
        if not tracing_was_active:
            tracemalloc.stop()
        storage_after = _directory_size(self._data_dir)

        errors = tuple(result.error for result in results if result.error is not None)
        completed = sum(result.error is None for result in results)
        initial_validated = sum(result.initial_validated for result in results)
        initial_activated = sum(result.initial_activated for result in results)
        replans_validated = sum(result.replan_validated for result in results)
        replans_activated = sum(result.replan_activated for result in results)
        evidence_resolved = sum(
            result.canonical_failure_evidence_resolved for result in results
        )
        distinct_replacements = sum(result.distinct_replacement_plan for result in results)
        passed = (
            not errors
            and completed == spec.operation_count
            and initial_validated == spec.operation_count
            and initial_activated == spec.operation_count
            and replans_validated == spec.operation_count
            and replans_activated == spec.operation_count
            and evidence_resolved == spec.operation_count
            and distinct_replacements == spec.operation_count
        )

        return PlanningPressureBenchmarkReport(
            schema_version=PLANNING_PRESSURE_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at,
            duration_seconds=round(duration_seconds, 6),
            environment=_environment_metadata(),
            throughput_lifecycles_per_second=round(completed / duration_seconds, 6),
            initial_proposal_latency=LatencyDistribution.from_seconds(
                _samples(results, "initial_proposal_seconds")
            ),
            initial_activation_latency=LatencyDistribution.from_seconds(
                _samples(results, "initial_activation_seconds")
            ),
            replan_proposal_latency=LatencyDistribution.from_seconds(
                _samples(results, "replan_proposal_seconds")
            ),
            replan_activation_latency=LatencyDistribution.from_seconds(
                _samples(results, "replan_activation_seconds")
            ),
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
            correctness=PlanningPressureCorrectnessSummary(
                requested_cycles=spec.operation_count,
                completed_cycles=completed,
                initial_proposals_validated=initial_validated,
                initial_plans_activated=initial_activated,
                terminal_replans_validated=replans_validated,
                replacement_plans_activated=replans_activated,
                canonical_failure_evidence_resolved=evidence_resolved,
                distinct_replacement_plans=distinct_replacements,
                provider_invocations=0,
                passed=passed,
            ),
            errors=errors,
        )


async def _ready_tasks(kernel: PlatformKernel, *, count: int, prefix: str) -> tuple[str, ...]:
    task_ids: list[str] = []
    for index in range(count):
        key = f"planning-pressure:{prefix}:{index}"
        task = await kernel.create_task(
            idempotency_key=f"{key}:create",
            title="Planning pressure benchmark",
            objective="Measure deterministic platform planning overhead",
            owner_type="service",
            owner_id="benchmark-planning-pressure",
        )
        ready = await kernel.ready_task(
            idempotency_key=f"{key}:ready",
            task_id=task.task_id,
        )
        task_ids.append(ready.task_id)
    return tuple(task_ids)


async def _exercise_cycle(
    *,
    planning: PlanningService,
    bridge: ReplanningEvidenceBridge,
    coordinator: DurablePlanStepCoordinator,
    lifecycle: FakeLifecycleBackend,
    kernel: PlatformKernel,
    task_id: str,
    key: str,
    timeout_seconds: float,
) -> _CycleResult:
    result = _CycleResult()
    try:
        started = time.perf_counter()
        initial = await asyncio.wait_for(
            planning.propose(task_id=task_id, idempotency_key=f"{key}:initial"),
            timeout=timeout_seconds,
        )
        result.initial_proposal_seconds = time.perf_counter() - started
        result.initial_validated = initial.validation.valid

        started = time.perf_counter()
        activated = await asyncio.wait_for(
            planning.activate(
                initial.proposal.proposal_id,
                idempotency_key=f"{key}:initial:activate",
            ),
            timeout=timeout_seconds,
        )
        result.initial_activation_seconds = time.perf_counter() - started
        result.initial_activated = activated.activation_plan_id is not None
        initial_plan_id = activated.activation_plan_id

        task = await kernel.get_task(task_id)
        if not task.run_ids:
            raise RuntimeError("activated benchmark Plan created no canonical Run")
        failed_run_id = task.run_ids[0]
        lifecycle.complete(failed_run_id, status=ExecutionStatus.FAILED)
        await kernel.refresh_run(
            idempotency_key=f"{key}:fail:refresh",
            task_id=task_id,
            run_id=failed_run_id,
        )
        await coordinator.observe_run(task_id=task_id, run_id=failed_run_id)

        started = time.perf_counter()
        replacement = await asyncio.wait_for(
            bridge.from_terminal_run(task_id=task_id, run_id=failed_run_id),
            timeout=timeout_seconds,
        )
        result.replan_proposal_seconds = time.perf_counter() - started
        result.replan_validated = replacement.validation.valid
        result.canonical_failure_evidence_resolved = (
            replacement.proposal.trigger is PlanningTrigger.TERMINAL_FAILURE
            and replacement.proposal.evidence_refs == (failed_run_id,)
        )

        started = time.perf_counter()
        reactivated = await asyncio.wait_for(
            planning.activate(
                replacement.proposal.proposal_id,
                idempotency_key=f"{key}:replacement:activate",
            ),
            timeout=timeout_seconds,
        )
        result.replan_activation_seconds = time.perf_counter() - started
        result.replan_activated = reactivated.activation_plan_id is not None
        result.distinct_replacement_plan = (
            initial_plan_id is not None
            and reactivated.activation_plan_id is not None
            and reactivated.activation_plan_id != initial_plan_id
        )
    except Exception as exc:  # benchmark evidence records failure instead of hiding it
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def _linear_draft(agent_id: str, revision: int, count: int) -> PlanDraft:
    steps: list[PlanningStepDraft] = []
    for index in range(count):
        key = f"step-{index + 1}"
        depends_on = () if index == 0 else (f"step-{index}",)
        steps.append(
            PlanningStepDraft(
                key=key,
                title=f"Deterministic planning Step {index + 1}",
                objective="Exercise platform-owned planning validation and handoff",
                depends_on=depends_on,
                assignment=AgentAssignment(agent_id=agent_id, agent_revision=revision),
            )
        )
    return PlanDraft(
        summary=f"Deterministic {count}-Step planning-pressure Plan",
        steps=tuple(steps),
    )


def _samples(results: list[_CycleResult], field: str) -> list[float]:
    samples: list[float] = []
    for result in results:
        value = getattr(result, field)
        if isinstance(value, float):
            samples.append(value)
    return samples
