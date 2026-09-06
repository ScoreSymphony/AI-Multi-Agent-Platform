from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts import PlanRequest, PlanResponse, PlanStepProposal
from ai_multi_agent_platform.coordination import (
    DurablePlanStepCoordinator,
    InMemoryCoordinatorRepository,
)
from ai_multi_agent_platform.domain import Plan, RunStatus, Step
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.observability import InMemoryExporter, Telemetry, TelemetryOutcome
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class DependentOrchestrator(FakeOrchestrator):
    async def plan(self, request: PlanRequest) -> PlanResponse:
        self.calls.append(request)
        return PlanResponse(
            summary=f"Observable plan for {request.objective}",
            steps=(
                PlanStepProposal(key="first", title="First", objective=request.objective),
                PlanStepProposal(
                    key="second",
                    title="Second",
                    objective=request.objective,
                    depends_on=("first",),
                ),
            ),
        )


async def _canonical_plan(
    kernel: PlatformKernel,
    *,
    key: str,
) -> tuple[Plan, tuple[Step, ...]]:
    created = await kernel.create_task(
        idempotency_key=f"{key}:create",
        title=f"{key} task",
        objective=f"{key} objective",
        owner_type="user",
        owner_id="observability-user",
    )
    await kernel.ready_task(idempotency_key=f"{key}:ready", task_id=created.task_id)
    planned = await kernel.plan_task(idempotency_key=f"{key}:plan", task_id=created.task_id)
    assert planned.plan_ref is not None
    assert len(planned.step_ids) == 2
    first_id, second_id = planned.step_ids
    plan = Plan(
        id=planned.plan_ref,
        task_id=planned.task_id,
        owner_ref=planned.task.owner_ref,
        active=True,
        project_id=planned.task.project_id,
    )
    return plan, (
        Step(
            id=first_id,
            plan_id=plan.id,
            title="First",
            owner_ref=plan.owner_ref,
            project_id=plan.project_id,
        ),
        Step(
            id=second_id,
            plan_id=plan.id,
            title="Second",
            owner_ref=plan.owner_ref,
            project_id=plan.project_id,
            depends_on=(first_id,),
        ),
    )


def test_attempt_barrier_and_cancellation_evidence_is_explicit() -> None:
    async def scenario() -> None:
        kernel = PlatformKernel(
            orchestrator=DependentOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=InMemoryKernelRepository(),
        )
        plan, steps = await _canonical_plan(kernel, key="explicit-evidence")
        exporter = InMemoryExporter()
        coordinator = DurablePlanStepCoordinator(
            repository=InMemoryCoordinatorRepository(),
            kernel=kernel,
            coordinator_id="observability-coordinator",
            telemetry=Telemetry(exporter),
        )

        projection = await coordinator.register_plan(plan, steps)
        first_run_id = projection.steps[0].latest_run_id
        assert first_run_id is not None
        initial = [entry.event_name for entry in exporter.timeline]
        assert "coordination.step.ready" in initial
        assert "coordination.attempt.created" in initial
        assert "coordination.attempt.dispatched" in initial
        assert "coordination.run.started" in initial

        await kernel.record_run_outcome(
            idempotency_key="explicit-evidence:first:succeeded",
            task_id=plan.task_id,
            run_id=first_run_id,
            status=RunStatus.SUCCEEDED,
        )
        projection = await coordinator.observe_run(
            task_id=plan.task_id,
            run_id=first_run_id,
            observation_key="explicit-evidence:first:observed",
        )
        second_run_id = projection.steps[1].latest_run_id
        assert second_run_id is not None

        names = [entry.event_name for entry in exporter.timeline]
        assert names.count("coordination.attempt.created") == 2
        assert names.count("coordination.attempt.dispatched") == 2
        assert "coordination.barrier.progress" in names
        assert "coordination.barrier.completed" in names
        completed = next(
            entry
            for entry in exporter.timeline
            if entry.event_name == "coordination.barrier.completed"
        )
        assert completed.context.step_id == steps[1].id
        assert completed.attributes["required"] == 1
        assert completed.outcome is TelemetryOutcome.SUCCEEDED

        await coordinator.cancel_plan(plan.id, idempotency_key="explicit-evidence:cancel")
        cancellation_names = [entry.event_name for entry in exporter.timeline]
        assert "coordination.step.cancelled" in cancellation_names
        assert "coordination.plan.cancelled" in cancellation_names
        cancelled = next(
            entry
            for entry in exporter.timeline
            if entry.event_name == "coordination.step.cancelled"
        )
        assert cancelled.context.step_id == steps[1].id
        assert cancelled.context.run_id == second_run_id
        assert cancelled.outcome is TelemetryOutcome.CANCELLED

    asyncio.run(scenario())


def test_claim_conflicts_are_emitted_from_the_shared_claim_boundary() -> None:
    async def scenario() -> None:
        kernel = PlatformKernel(
            orchestrator=DependentOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=InMemoryKernelRepository(),
        )
        plan, steps = await _canonical_plan(kernel, key="claim-evidence")
        repository = InMemoryCoordinatorRepository()
        exporter = InMemoryExporter()
        coordinator = DurablePlanStepCoordinator(
            repository=repository,
            kernel=kernel,
            coordinator_id="claim-observer",
            telemetry=Telemetry(exporter),
        )
        projection = await coordinator.register_plan(plan, steps)
        run_id = projection.steps[0].latest_run_id
        assert run_id is not None

        now = datetime.now(UTC)
        foreign = repository.acquire_claim(
            step_id=steps[0].id,
            owner_id="other-coordinator",
            ttl=timedelta(minutes=1),
            now=now,
        )
        assert foreign is not None
        await kernel.record_run_outcome(
            idempotency_key="claim-evidence:first:succeeded",
            task_id=plan.task_id,
            run_id=run_id,
            status=RunStatus.SUCCEEDED,
        )
        unchanged = await coordinator.observe_run(
            task_id=plan.task_id,
            run_id=run_id,
            observation_key="claim-evidence:first:observed",
            now=now,
        )

        assert unchanged.steps[0].latest_run_id == run_id
        conflicts = [
            entry for entry in exporter.timeline if entry.event_name == "coordination.claim.conflict"
        ]
        assert len(conflicts) == 1
        assert conflicts[0].context.step_id == steps[0].id
        assert conflicts[0].outcome is TelemetryOutcome.FAILED
        assert conflicts[0].attributes["coordinator_id"] == "claim-observer"

    asyncio.run(scenario())
