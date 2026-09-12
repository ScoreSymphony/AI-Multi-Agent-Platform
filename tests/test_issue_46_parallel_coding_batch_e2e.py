from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pytest

from ai_multi_agent_platform.agents import (
    AgentRevisionRef,
    AgentRunRecord,
    AgentRunStatus,
    new_agent_id,
    new_agent_run_id,
)
from ai_multi_agent_platform.coding_batches import (
    AuthorizedCodingBatchIntegration,
    CheckState,
    CodingBatchAuthorizationContext,
    CodingBatchCoordinator,
    CodingBatchRepairCoordinator,
    CodingWorkItem,
    CombinedValidationEvidence,
    InMemoryCodingBatchStore,
    IntegrationState,
    RequiredCheck,
    VerificationEvidence,
    WorkstreamResult,
    WorkstreamState,
)
from ai_multi_agent_platform.coding_batches.runtime import CanonicalCodingWorkstreamDispatcher
from ai_multi_agent_platform.contracts import JsonValue, OperationContext
from ai_multi_agent_platform.coordination import (
    DurablePlanStepCoordinator,
    InMemoryCoordinatorRepository,
)
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.domain import (
    OwnerRef,
    Plan,
    Run,
    RunStatus,
    Step,
    Task,
    TaskStatus,
    new_id,
)
from ai_multi_agent_platform.kernel.models import RecoveryReport, RunState, TaskState
from ai_multi_agent_platform.repositories import RepositoryCallContext
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)

BASE = "a" * 40
REV_A = "1" * 40
REV_B = "2" * 40
REV_C = "3" * 40
INTEGRATED = "4" * 40
REPAIRED = "5" * 40
ACTOR_REF = "human:issue-872-e2e"


class _Kernel:
    """Small canonical Run backend for exercising the real #384 coordinator."""

    def __init__(self, plan: Plan, steps: tuple[Step, ...]) -> None:
        self.task = TaskState(
            task=Task(
                id=plan.task_id,
                title="parallel coding batch",
                owner_ref=plan.owner_ref,
                project_id=plan.project_id,
                status=TaskStatus.READY,
            ),
            revision=1,
            plan_ref=plan.id,
            step_ids=tuple(step.id for step in steps),
        )
        self.runs: dict[str, RunState] = {}
        self.by_key: dict[str, str] = {}
        self.started: set[str] = set()

    async def get_task(self, task_id: str) -> TaskState:
        assert task_id == self.task.task_id
        return self.task

    async def get_run(self, task_id: str, run_id: str) -> RunState:
        assert task_id == self.task.task_id
        return self.runs[run_id]

    async def create_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        subject_type: str = "task",
        subject_id: str | None = None,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> RunState:
        del actor_ref, source
        assert task_id == self.task.task_id
        if idempotency_key in self.by_key:
            return self.runs[self.by_key[idempotency_key]]
        assert subject_type == "step"
        assert subject_id is not None
        run = Run(
            subject_type="step",
            subject_id=subject_id,
            owner_ref=self.task.task.owner_ref,
            correlation_id=task_id,
            project_id=self.task.task.project_id,
        )
        state = RunState(run=run, revision=1)
        self.runs[run.id] = state
        self.by_key[idempotency_key] = run.id
        return state

    async def start_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> RunState:
        del actor_ref, source
        current = await self.get_run(task_id, run_id)
        if idempotency_key in self.started:
            return current
        self.started.add(idempotency_key)
        running = replace(
            current,
            run=replace(current.run, status=RunStatus.RUNNING),
            revision=current.revision + 1,
        )
        self.runs[run_id] = running
        if self.task.status is TaskStatus.READY:
            self.task = replace(
                self.task,
                task=replace(self.task.task, status=TaskStatus.RUNNING),
                revision=self.task.revision + 1,
            )
        return running

    async def cancel_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> RunState:
        del idempotency_key, actor_ref, source
        current = await self.get_run(task_id, run_id)
        cancelled = replace(
            current,
            run=replace(current.run, status=RunStatus.CANCELLED),
            revision=current.revision + 1,
        )
        self.runs[run_id] = cancelled
        return cancelled

    async def complete_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState:
        del idempotency_key, actor_ref, source
        assert task_id == self.task.task_id
        self.task = replace(
            self.task,
            task=replace(self.task.task, status=TaskStatus.SUCCEEDED),
            revision=self.task.revision + 1,
        )
        return self.task

    async def fail_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        reason: str | None = None,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState:
        del idempotency_key, reason, actor_ref, source
        assert task_id == self.task.task_id
        self.task = replace(
            self.task,
            task=replace(self.task.task, status=TaskStatus.FAILED),
            revision=self.task.revision + 1,
        )
        return self.task

    async def cancel_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState:
        del idempotency_key, actor_ref, source
        assert task_id == self.task.task_id
        self.task = replace(
            self.task,
            task=replace(self.task.task, status=TaskStatus.CANCELLED),
            revision=self.task.revision + 1,
        )
        return self.task

    async def recover_task(self, task_id: str) -> RecoveryReport:
        assert task_id == self.task.task_id
        return RecoveryReport(task_id=task_id, entries=())

    def finish(self, run_id: str, status: RunStatus) -> None:
        current = self.runs[run_id]
        self.runs[run_id] = replace(
            current,
            run=replace(current.run, status=status),
            revision=current.revision + 1,
        )


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
        record = AgentRunRecord(
            agent_run_id=new_agent_run_id(),
            run_id=run_id,
            task_id=task_id,
            agent=AgentRevisionRef(agent_id=agent_id, revision=revision or 1),
            status=AgentRunStatus.RUNNING,
            verification_context=verification_context or {},
        )
        self.runs.records.append(record)
        return record


class _Materializer:
    def __init__(self, coordinator: CodingBatchCoordinator) -> None:
        self.coordinator = coordinator

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
        current = self.coordinator.get(batch_id).workstream(workstream_id)
        if current.provenance.workspace_id is not None:
            return current
        return self.coordinator.materialize_workstream(
            batch_id,
            workstream_id,
            workspace_id=new_id("workspace"),
            snapshot_id=new_id("snapshot"),
            agent_revision=agent_revision,
            agent_run_id=agent_run_id,
        )


def _operation(owner: OwnerRef, project_id: str) -> OperationContext:
    return OperationContext(
        correlation_id="issue-872-e2e",
        owner_type=owner.type,
        owner_id=owner.id,
        project_id=project_id,
    )


def _contexts(
    owner: OwnerRef,
    project_id: str,
    task_id: str,
    run_id: str,
    agent_id: str,
) -> tuple[DataAccessContext, RepositoryCallContext]:
    operation = _operation(owner, project_id)
    return (
        DataAccessContext(
            operation=operation,
            actor_ref="issue-872-e2e",
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
        ),
        RepositoryCallContext(
            operation=operation,
            actor_ref="issue-872-e2e",
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
        ),
    )


def _accept(
    coordinator: CodingBatchCoordinator,
    batch_id: str,
    workstream_id: str,
    revision: str,
    path: str,
) -> None:
    coordinator.record_result(
        batch_id,
        workstream_id,
        WorkstreamResult(
            output_revision=revision,
            changed_paths=(path,),
            diff_digest=f"digest-{workstream_id}",
        ),
    )
    coordinator.record_verification(
        batch_id,
        workstream_id,
        VerificationEvidence(
            verification_id=f"verification-{workstream_id}",
            subject_revision=revision,
            passed=True,
        ),
    )
    coordinator.accept_workstream(batch_id, workstream_id)


def _authorization_gate() -> AuthorizationGate:
    return AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref=ACTOR_REF,
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.MODIFY}),
                    resource_types=frozenset({ResourceType.GENERIC}),
                ),
            )
        )
    )


@pytest.mark.asyncio
async def test_parallel_coding_batch_follows_384_fanout_fanin_and_reaches_authorized_merge_ready() -> (
    None
):
    owner = OwnerRef(type="user", id="issue-872-e2e")
    project_id = new_id("project")
    plan = Plan(
        task_id=new_id("task"),
        owner_ref=owner,
        active=True,
        project_id=project_id,
    )
    step_a = Step(
        plan_id=plan.id,
        title="A",
        owner_ref=owner,
        project_id=project_id,
    )
    step_b = Step(
        plan_id=plan.id,
        title="B",
        owner_ref=owner,
        project_id=project_id,
    )
    step_c = Step(
        plan_id=plan.id,
        title="C after A",
        owner_ref=owner,
        project_id=project_id,
        depends_on=(step_a.id,),
    )
    kernel = _Kernel(plan, (step_a, step_b, step_c))
    coordination = DurablePlanStepCoordinator(
        repository=InMemoryCoordinatorRepository(),
        kernel=kernel,
        coordinator_id="issue-872-e2e",
    )
    projection = await coordination.register_plan(plan, (step_a, step_b, step_c))
    initial = {step.step_id: step for step in projection.steps}
    assert initial[step_a.id].latest_run_id is not None
    assert initial[step_b.id].latest_run_id is not None
    assert initial[step_c.id].latest_run_id is None

    store = InMemoryCodingBatchStore()
    coding = CodingBatchCoordinator(store)
    batch = coding.create_batch(
        request_key="issue-872-e2e-three-workstreams",
        repository_id="repo-platform",
        target_ref="main",
        base_revision=BASE,
        work_items=(
            CodingWorkItem(
                work_item_id="A",
                task_id=plan.task_id,
                plan_id=plan.id,
                step_id=step_a.id,
                affected_paths=("src/a.py",),
                semantic_scopes=("module:a",),
            ),
            CodingWorkItem(
                work_item_id="B",
                task_id=plan.task_id,
                plan_id=plan.id,
                step_id=step_b.id,
                affected_paths=("frontend/b.ts",),
                semantic_scopes=("module:b",),
            ),
            CodingWorkItem(
                work_item_id="C",
                task_id=plan.task_id,
                plan_id=plan.id,
                step_id=step_c.id,
                dependencies=("A",),
                affected_paths=("docs/c.md",),
                semantic_scopes=("module:c",),
            ),
        ),
    )
    runs = _AgentRuns()
    runtime = _AgentRuntime(runs)
    dispatcher = CanonicalCodingWorkstreamDispatcher(
        coding,
        plan_coordination=coordination,
        agent_runtime=runtime,
        agent_runs=runs,
        materializer=_Materializer(coding),
    )

    initial_slots = dispatcher.dispatchable_workstreams(batch.batch_id)
    assert {slot.workstream_id for slot in initial_slots} == {"A", "B"}
    agent_a = new_agent_id()
    agent_b = new_agent_id()
    for workstream_id, agent_id in (("A", agent_a), ("B", agent_b)):
        slot = next(item for item in initial_slots if item.workstream_id == workstream_id)
        data_context, repository_context = _contexts(
            owner,
            project_id,
            plan.task_id,
            slot.run_id,
            agent_id,
        )
        dispatched = await dispatcher.ensure_dispatched(
            batch.batch_id,
            workstream_id,
            agent_id=agent_id,
            project_id=project_id,
            owner_ref=owner,
            data_context=data_context,
            repository_context=repository_context,
        )
        assert dispatched.workstream.state is WorkstreamState.RUNNING
        assert dispatched.workstream.provenance.workspace_id is not None
        assert dispatched.workstream.provenance.agent_run_id == dispatched.agent_run.agent_run_id

    assert coding.get(batch.batch_id).workstream("C").state is WorkstreamState.BLOCKED
    assert (
        len(
            {
                coding.get(batch.batch_id).workstream(item).provenance.workspace_id
                for item in ("A", "B")
            }
        )
        == 2
    )

    _accept(coding, batch.batch_id, "A", REV_A, "src/a.py")
    run_a = initial[step_a.id].latest_run_id
    assert run_a is not None
    kernel.finish(run_a, RunStatus.SUCCEEDED)
    await coordination.observe_run(task_id=plan.task_id, run_id=run_a)

    after_a = {step.step_id: step for step in coordination.projection(plan.id).steps}
    assert after_a[step_c.id].latest_run_id is not None
    assert coding.get(batch.batch_id).workstream("C").state is WorkstreamState.READY

    slot_c = next(
        slot
        for slot in dispatcher.dispatchable_workstreams(batch.batch_id)
        if slot.workstream_id == "C"
    )
    agent_c = new_agent_id()
    data_context, repository_context = _contexts(
        owner,
        project_id,
        plan.task_id,
        slot_c.run_id,
        agent_c,
    )
    await dispatcher.ensure_dispatched(
        batch.batch_id,
        "C",
        agent_id=agent_c,
        project_id=project_id,
        owner_ref=owner,
        data_context=data_context,
        repository_context=repository_context,
    )

    _accept(coding, batch.batch_id, "B", REV_B, "frontend/b.ts")
    _accept(coding, batch.batch_id, "C", REV_C, "docs/c.md")
    candidate = coding.build_integration_candidate(
        batch.batch_id,
        current_target_revision=BASE,
    )
    assert candidate.ordered_workstream_ids == ("A", "B", "C")
    assert candidate.state is IntegrationState.READY

    coding.record_integrated_revision(
        batch.batch_id,
        candidate.integration_id,
        integrated_revision=INTEGRATED,
    )
    validated = coding.record_combined_validation(
        batch.batch_id,
        candidate.integration_id,
        CombinedValidationEvidence(
            subject_revision=INTEGRATED,
            verification_id="verification-combined-e2e",
            tests_passed=True,
            required_checks=(
                RequiredCheck("unit", INTEGRATED, CheckState.PASS),
                RequiredCheck("typecheck", INTEGRATED, CheckState.PASS),
            ),
        ),
    )
    assert validated.state is IntegrationState.VALIDATED

    authorized = AuthorizedCodingBatchIntegration(coding, _authorization_gate())
    ready = await authorized.mark_merge_ready(
        batch.batch_id,
        candidate.integration_id,
        context=CodingBatchAuthorizationContext(
            actor=ActorIdentity(ACTOR_REF, ActorType.HUMAN),
            operation=_operation(owner, project_id),
        ),
    )
    assert ready.state is IntegrationState.MERGE_READY


def test_conflicting_valid_workstreams_require_canonical_repair_and_fresh_combined_validation() -> (
    None
):
    owner = OwnerRef(type="user", id="issue-872-repair")
    task_id = new_id("task")
    plan_id = new_id("plan")
    step_a = new_id("step")
    step_b = new_id("step")
    store = InMemoryCodingBatchStore()
    coding = CodingBatchCoordinator(store)
    batch = coding.create_batch(
        request_key="issue-872-e2e-conflict",
        repository_id="repo-platform",
        target_ref="main",
        base_revision=BASE,
        work_items=(
            CodingWorkItem(
                work_item_id="A",
                task_id=task_id,
                plan_id=plan_id,
                step_id=step_a,
                affected_paths=("src/a.py",),
                semantic_scopes=("shared-contract",),
            ),
            CodingWorkItem(
                work_item_id="B",
                task_id=task_id,
                plan_id=plan_id,
                step_id=step_b,
                affected_paths=("src/b.py",),
                semantic_scopes=("shared-contract",),
            ),
        ),
    )
    for workstream_id, revision, path in (
        ("A", REV_A, "src/a.py"),
        ("B", REV_B, "src/b.py"),
    ):
        if coding.get(batch.batch_id).workstream(workstream_id).state is WorkstreamState.BLOCKED:
            predecessor = "A"
            assert (
                coding.get(batch.batch_id).workstream(predecessor).state is WorkstreamState.ACCEPTED
            )
        coding.materialize_workstream(
            batch.batch_id,
            workstream_id,
            workspace_id=new_id("workspace"),
            snapshot_id=new_id("snapshot"),
            agent_revision=f"agent-{workstream_id}@1",
            agent_run_id=f"agent-run-{workstream_id}",
        )
        coding.start_workstream(batch.batch_id, workstream_id)
        _accept(coding, batch.batch_id, workstream_id, revision, path)

    candidate = coding.build_integration_candidate(
        batch.batch_id,
        current_target_revision=BASE,
    )
    assert candidate.state is IntegrationState.BLOCKED
    assert candidate.conflicts

    repair_plan = Plan(task_id=task_id, owner_ref=owner, active=True)
    repair_step = Step(
        plan_id=repair_plan.id,
        title="repair semantic integration conflict",
        owner_ref=owner,
    )
    repairs = CodingBatchRepairCoordinator(store, max_attempts=2)
    attempt = repairs.bind_repair_step(
        batch.batch_id,
        candidate.integration_id,
        repair_plan=repair_plan,
        repair_step=repair_step,
        target_revision=BASE,
    )
    repaired = repairs.record_repair_result(
        batch.batch_id,
        candidate.integration_id,
        attempt.repair_id,
        output_revision=REPAIRED,
        verification=VerificationEvidence(
            verification_id="verification-repair-e2e",
            subject_revision=REPAIRED,
            passed=True,
        ),
    )
    assert repaired.state is IntegrationState.VALIDATING
    assert repaired.validation is None

    validated = coding.record_combined_validation(
        batch.batch_id,
        candidate.integration_id,
        CombinedValidationEvidence(
            subject_revision=REPAIRED,
            verification_id="verification-repaired-combined-e2e",
            tests_passed=True,
            required_checks=(RequiredCheck("unit", REPAIRED, CheckState.PASS),),
        ),
    )
    assert validated.state is IntegrationState.VALIDATED
