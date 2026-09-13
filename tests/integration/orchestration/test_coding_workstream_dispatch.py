from __future__ import annotations

from collections.abc import Mapping

import pytest

from ai_multi_agent_platform.agents import (
    AgentRevisionRef,
    AgentRunRecord,
    AgentRunStatus,
    new_agent_id,
    new_agent_run_id,
)
from ai_multi_agent_platform.coding_batches import (
    CodingBatchCoordinator,
    CodingWorkItem,
    InMemoryCodingBatchStore,
    WorkstreamState,
)
from ai_multi_agent_platform.coding_batches.runtime import CanonicalCodingWorkstreamDispatcher
from ai_multi_agent_platform.contracts import JsonValue, OperationContext
from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    PlanCoordinationProjection,
    ReconciliationDisposition,
    StepCoordinationProjection,
)
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.domain import OwnerRef, StepStatus, new_id
from ai_multi_agent_platform.repositories import RepositoryCallContext


class _PlanCoordination:
    def __init__(self, projections: Mapping[str, PlanCoordinationProjection]) -> None:
        self.projections = dict(projections)

    def projection(self, plan_id: str) -> PlanCoordinationProjection:
        return self.projections[plan_id]


class _AgentRuns:
    def __init__(self) -> None:
        self.records: list[AgentRunRecord] = []

    def list_agent_runs(self, run_id: str | None = None) -> tuple[AgentRunRecord, ...]:
        if run_id is None:
            return tuple(self.records)
        return tuple(record for record in self.records if record.run_id == run_id)


class _AgentRuntime:
    def __init__(self, runs: _AgentRuns) -> None:
        self.runs = runs
        self.start_calls = 0

    async def start_agent(
        self,
        *,
        task_id: str,
        run_id: str,
        agent_id: str,
        revision: int | None = None,
        requested_capability_ids: tuple[str, ...] = (),
        available_capability_ids: frozenset[str] = frozenset(),
        granted_permissions: frozenset[str] = frozenset(),
        available_worker_capabilities: frozenset[str] = frozenset(),
        task_context: Mapping[str, JsonValue] | None = None,
        project_context: Mapping[str, JsonValue] | None = None,
        verification_context: Mapping[str, JsonValue] | None = None,
    ) -> AgentRunRecord:
        del (
            requested_capability_ids,
            available_capability_ids,
            granted_permissions,
            available_worker_capabilities,
            task_context,
            project_context,
        )
        self.start_calls += 1
        record = AgentRunRecord(
            agent_run_id=new_agent_run_id(),
            run_id=run_id,
            task_id=task_id,
            agent=AgentRevisionRef(agent_id=agent_id, revision=revision or 1),
            status=AgentRunStatus.STARTING,
            verification_context=verification_context or {},
        )
        self.runs.records.append(record)
        return record


class _Materializer:
    def __init__(self, coordinator: CodingBatchCoordinator) -> None:
        self.coordinator = coordinator
        self.calls = 0

    async def ensure_materialized(
        self,
        batch_id: str,
        workstream_id: str,
        *,
        project_id: str,
        owner_ref: OwnerRef,
        data_context: DataAccessContext,
        repository_context: RepositoryCallContext,
        agent_revision: str,
        agent_run_id: str,
    ):
        del project_id, owner_ref, data_context, repository_context
        self.calls += 1
        current = self.coordinator.get(batch_id).workstream(workstream_id)
        if current.provenance.workspace_id is not None:
            assert current.provenance.agent_revision == agent_revision
            assert current.provenance.agent_run_id == agent_run_id
            return current
        return self.coordinator.materialize_workstream(
            batch_id,
            workstream_id,
            workspace_id=new_id("workspace"),
            snapshot_id=new_id("snapshot"),
            agent_revision=agent_revision,
            agent_run_id=agent_run_id,
        )


def _projection(
    *,
    task_id: str,
    plan_id: str,
    plan_revision: int,
    steps: tuple[tuple[str, str, tuple[str, ...]], ...],
) -> PlanCoordinationProjection:
    return PlanCoordinationProjection(
        task_id=task_id,
        plan_id=plan_id,
        plan_revision=plan_revision,
        steps=tuple(
            StepCoordinationProjection(
                step_id=step_id,
                status=StepStatus.RUNNING,
                phase=CoordinationPhase.ATTEMPT_ACTIVE,
                dependency_ids=dependencies,
                satisfied_dependency_ids=dependencies,
                latest_run_id=run_id,
                current_attempt=1,
                retry_due_at=None,
                wait_type=None,
                wait_deadline_at=None,
                reconciliation=ReconciliationDisposition.CONSISTENT,
            )
            for step_id, run_id, dependencies in steps
        ),
    )


def _contexts(
    *,
    task_id: str,
    run_id: str,
    agent_id: str,
) -> tuple[str, OwnerRef, DataAccessContext, RepositoryCallContext]:
    project_id = new_id("project")
    owner = OwnerRef(type="user", id="owner-872")
    operation = OperationContext(
        correlation_id="coding-batch-872",
        owner_type=owner.type,
        owner_id=owner.id,
        project_id=project_id,
    )
    return (
        project_id,
        owner,
        DataAccessContext(
            operation=operation,
            actor_ref="actor-872",
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
        ),
        RepositoryCallContext(
            operation=operation,
            actor_ref="actor-872",
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
        ),
    )


@pytest.mark.asyncio
async def test_dispatch_binds_384_run_to_33_agentrun_then_workspace_and_reuses_on_restart() -> None:
    store = InMemoryCodingBatchStore()
    coordinator = CodingBatchCoordinator(store)
    task_id = new_id("task")
    plan_id = new_id("plan")
    step_id = new_id("step")
    run_id = new_id("run")
    batch = coordinator.create_batch(
        request_key="issue-872-runtime-dispatch",
        repository_id="repo-872",
        target_ref="main",
        base_revision="base-a",
        work_items=(
            CodingWorkItem(
                work_item_id="A",
                task_id=task_id,
                plan_id=plan_id,
                step_id=step_id,
                affected_paths=("src/a.py",),
                semantic_scopes=("module:a",),
            ),
        ),
    )
    plan_coordination = _PlanCoordination(
        {
            plan_id: _projection(
                task_id=task_id,
                plan_id=plan_id,
                plan_revision=7,
                steps=((step_id, run_id, ()),),
            )
        }
    )
    runs = _AgentRuns()
    runtime = _AgentRuntime(runs)
    materializer = _Materializer(coordinator)
    dispatcher = CanonicalCodingWorkstreamDispatcher(
        coordinator,
        plan_coordination=plan_coordination,
        agent_runtime=runtime,
        agent_runs=runs,
        materializer=materializer,
    )
    agent_id = new_agent_id()
    project_id, owner, data_context, repository_context = _contexts(
        task_id=task_id,
        run_id=run_id,
        agent_id=agent_id,
    )

    slots = dispatcher.dispatchable_workstreams(batch.batch_id)
    assert len(slots) == 1
    assert slots[0].run_id == run_id
    assert slots[0].plan_revision == 7

    first = await dispatcher.ensure_dispatched(
        batch.batch_id,
        "A",
        agent_id=agent_id,
        agent_revision=1,
        project_id=project_id,
        owner_ref=owner,
        data_context=data_context,
        repository_context=repository_context,
    )

    assert first.workstream.state is WorkstreamState.RUNNING
    assert first.workstream.provenance.agent_run_id == first.agent_run.agent_run_id
    assert first.workstream.provenance.agent_revision == f"{agent_id}@1"
    assert first.workstream.provenance.workspace_id is not None
    assert first.agent_run.verification_context["coding_batch_id"] == batch.batch_id
    assert first.agent_run.verification_context["plan_id"] == plan_id
    assert first.agent_run.verification_context["plan_revision"] == 7
    assert first.agent_run.verification_context["step_id"] == step_id
    assert first.agent_run.verification_context["base_revision"] == "base-a"
    assert runtime.start_calls == 1

    restarted = CanonicalCodingWorkstreamDispatcher(
        CodingBatchCoordinator(store),
        plan_coordination=plan_coordination,
        agent_runtime=runtime,
        agent_runs=runs,
        materializer=_Materializer(CodingBatchCoordinator(store)),
    )
    second = await restarted.ensure_dispatched(
        batch.batch_id,
        "A",
        agent_id=agent_id,
        agent_revision=1,
        project_id=project_id,
        owner_ref=owner,
        data_context=data_context,
        repository_context=repository_context,
    )

    assert second.agent_run.agent_run_id == first.agent_run.agent_run_id
    assert second.workstream.provenance.agent_run_id == first.agent_run.agent_run_id
    assert runtime.start_calls == 1


def test_dispatchable_intersects_384_attempts_with_conservative_overlap_gate() -> None:
    coordinator = CodingBatchCoordinator()
    task_id = new_id("task")
    plan_id = new_id("plan")
    step_a = new_id("step")
    step_b = new_id("step")
    run_a = new_id("run")
    run_b = new_id("run")
    batch = coordinator.create_batch(
        request_key="issue-872-runtime-overlap",
        repository_id="repo-872",
        target_ref="main",
        base_revision="base-a",
        work_items=(
            CodingWorkItem(
                work_item_id="A",
                task_id=task_id,
                plan_id=plan_id,
                step_id=step_a,
                affected_paths=("src/shared.py",),
            ),
            CodingWorkItem(
                work_item_id="B",
                task_id=task_id,
                plan_id=plan_id,
                step_id=step_b,
                affected_paths=("src/shared.py",),
            ),
        ),
    )
    projection = _projection(
        task_id=task_id,
        plan_id=plan_id,
        plan_revision=2,
        steps=((step_a, run_a, ()), (step_b, run_b, ())),
    )
    runs = _AgentRuns()
    dispatcher = CanonicalCodingWorkstreamDispatcher(
        coordinator,
        plan_coordination=_PlanCoordination({plan_id: projection}),
        agent_runtime=_AgentRuntime(runs),
        agent_runs=runs,
        materializer=_Materializer(coordinator),
    )

    slots = dispatcher.dispatchable_workstreams(batch.batch_id)

    assert tuple(slot.workstream_id for slot in slots) == ("A",)
    assert coordinator.get(batch.batch_id).workstream("B").state is WorkstreamState.BLOCKED


@pytest.mark.asyncio
async def test_dispatch_fails_closed_when_selected_dependency_graph_diverges_from_384() -> None:
    coordinator = CodingBatchCoordinator()
    task_id = new_id("task")
    plan_id = new_id("plan")
    step_a = new_id("step")
    step_b = new_id("step")
    run_a = new_id("run")
    run_b = new_id("run")
    batch = coordinator.create_batch(
        request_key="issue-872-runtime-dependency",
        repository_id="repo-872",
        target_ref="main",
        base_revision="base-a",
        work_items=(
            CodingWorkItem(
                work_item_id="A",
                task_id=task_id,
                plan_id=plan_id,
                step_id=step_a,
                affected_paths=("src/a.py",),
                semantic_scopes=("module:a",),
            ),
            CodingWorkItem(
                work_item_id="B",
                task_id=task_id,
                plan_id=plan_id,
                step_id=step_b,
                affected_paths=("src/b.py",),
                semantic_scopes=("module:b",),
            ),
        ),
    )
    projection = _projection(
        task_id=task_id,
        plan_id=plan_id,
        plan_revision=3,
        steps=((step_a, run_a, ()), (step_b, run_b, (step_a,))),
    )
    runs = _AgentRuns()
    dispatcher = CanonicalCodingWorkstreamDispatcher(
        coordinator,
        plan_coordination=_PlanCoordination({plan_id: projection}),
        agent_runtime=_AgentRuntime(runs),
        agent_runs=runs,
        materializer=_Materializer(coordinator),
    )
    agent_id = new_agent_id()
    project_id, owner, data_context, repository_context = _contexts(
        task_id=task_id,
        run_id=run_b,
        agent_id=agent_id,
    )

    with pytest.raises(ValueError, match="dependencies diverge"):
        await dispatcher.ensure_dispatched(
            batch.batch_id,
            "B",
            agent_id=agent_id,
            project_id=project_id,
            owner_ref=owner,
            data_context=data_context,
            repository_context=repository_context,
        )
