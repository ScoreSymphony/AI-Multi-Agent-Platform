from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, ExecutionStatus, JsonValue
from ai_multi_agent_platform.coordination import (
    DurablePlanStepCoordinator,
    InMemoryCoordinatorRepository,
)
from ai_multi_agent_platform.domain import OwnerRef, TaskStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.planning import (
    AgentAssignment,
    DeterministicReferencePlanner,
    InMemoryPlanningRepository,
    PlanDraft,
    PlannerDescriptor,
    PlannerKind,
    PlannerOutput,
    PlanningOrchestratorAdapter,
    PlanningRequest,
    PlanningService,
    PlanningStepDraft,
    PlanningTrigger,
    ReplanningEvidenceBridge,
    ReplanPolicy,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.verification import VerificationOutcome
from ai_multi_agent_platform.verification.audit import (
    VerificationAuditEvent,
    VerificationAuditEventType,
)

OWNER = OwnerRef(type="user", id="issue-439-evidence-user")


class _VerificationEvidenceStore:
    def __init__(self, events: tuple[VerificationAuditEvent, ...]) -> None:
        self._events = events

    def audit_history(
        self,
        *,
        task_id: str | None = None,
        verification_id: str | None = None,
    ) -> tuple[VerificationAuditEvent, ...]:
        return tuple(
            event
            for event in self._events
            if (task_id is None or event.task_id == task_id)
            and (verification_id is None or event.verification_id == verification_id)
        )


class _ExplodingPlanner:
    @property
    def descriptor(self) -> PlannerDescriptor:
        return PlannerDescriptor("exploding-planner", PlannerKind.DETERMINISTIC)

    async def propose(self, request: PlanningRequest) -> PlannerOutput:
        del request
        raise TimeoutError("sensitive planner backend detail")


def _agent_repository() -> tuple[InMemoryAgentRepository, str, int]:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    revision = service.create_agent(
        AgentProfile(
            name="Evidence replanning worker",
            role="worker",
            instructions=AgentInstructions(
                role=InstructionSource(content="Execute one planned Step.", version="1")
            ),
        ),
        owner_ref=OWNER,
    )
    return repository, revision.agent_id, revision.revision


def _draft(agent_id: str, revision: int, *, title: str = "Execute work") -> PlanDraft:
    return PlanDraft(
        summary="Issue 439 evidence plan",
        steps=(
            PlanningStepDraft(
                key="step-1",
                title=title,
                objective="complete canonical work",
                assignment=AgentAssignment(agent_id=agent_id, agent_revision=revision),
            ),
        ),
    )


async def _ready_task(kernel: PlatformKernel, key: str) -> str:
    task = await kernel.create_task(
        idempotency_key=f"{key}:create",
        title="Issue 439 evidence",
        objective="Exercise canonical evidence-driven bounded replanning",
        owner_type="user",
        owner_id=OWNER.id,
    )
    ready = await kernel.ready_task(
        idempotency_key=f"{key}:ready",
        task_id=task.task_id,
    )
    return ready.task_id


async def _failed_step_run(
    kernel: PlatformKernel,
    lifecycle: FakeLifecycleBackend,
    *,
    task_id: str,
    step_id: str,
    key: str,
):
    run = await kernel.create_run(
        idempotency_key=f"{key}:create",
        task_id=task_id,
        subject_type="step",
        subject_id=step_id,
    )
    await kernel.start_run(
        idempotency_key=f"{key}:start",
        task_id=task_id,
        run_id=run.run_id,
    )
    lifecycle.complete(run.run_id, status=ExecutionStatus.FAILED)
    return await kernel.refresh_run(
        idempotency_key=f"{key}:refresh",
        task_id=task_id,
        run_id=run.run_id,
    )


def test_terminal_run_failure_triggers_one_idempotent_replan() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agent_repository()
        repository = InMemoryPlanningRepository()
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=PlanningOrchestratorAdapter(repository, fallback=FakeOrchestrator()),
            lifecycle=lifecycle,
            repository=InMemoryKernelRepository(),
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=repository,
            kernel=kernel,
            agents=agents,
        )
        task_id = await _ready_task(kernel, "run-failure")
        initial = await planning.propose(
            task_id=task_id,
            idempotency_key="run-failure:initial",
        )
        await planning.activate(
            initial.proposal.proposal_id,
            idempotency_key="run-failure:activate",
        )

        task = await kernel.get_task(task_id)
        run = await _failed_step_run(
            kernel,
            lifecycle,
            task_id=task_id,
            step_id=task.step_ids[0],
            key="run-failure",
        )

        bridge = ReplanningEvidenceBridge(planning)
        first = await bridge.from_terminal_run(task_id=task_id, run_id=run.run_id)
        duplicate = await bridge.from_terminal_run(task_id=task_id, run_id=run.run_id)

        assert first.proposal.proposal_id == duplicate.proposal.proposal_id
        assert first.proposal.trigger is PlanningTrigger.TERMINAL_FAILURE
        assert first.proposal.evidence_refs == (run.run_id,)
        assert first.proposal.base_plan_id == task.plan_ref
        assert len(planning.history(task_id)) == 2

    asyncio.run(scenario())


def test_terminal_run_rejects_failure_superseded_by_later_attempt() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agent_repository()
        repository = InMemoryPlanningRepository()
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=PlanningOrchestratorAdapter(repository, fallback=FakeOrchestrator()),
            lifecycle=lifecycle,
            repository=InMemoryKernelRepository(),
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=repository,
            kernel=kernel,
            agents=agents,
        )
        task_id = await _ready_task(kernel, "superseded-attempt")
        initial = await planning.propose(
            task_id=task_id,
            idempotency_key="superseded-attempt:initial",
        )
        await planning.activate(
            initial.proposal.proposal_id,
            idempotency_key="superseded-attempt:activate",
        )
        task = await kernel.get_task(task_id)
        step_id = task.step_ids[0]
        failed = await _failed_step_run(
            kernel,
            lifecycle,
            task_id=task_id,
            step_id=step_id,
            key="superseded-attempt:first",
        )

        current = await kernel.get_task(task_id)
        if current.status is TaskStatus.FAILED:
            await kernel.ready_task(
                idempotency_key="superseded-attempt:ready",
                task_id=task_id,
            )
        retry = await kernel.create_run(
            idempotency_key="superseded-attempt:retry:create",
            task_id=task_id,
            subject_type="step",
            subject_id=step_id,
        )
        await kernel.start_run(
            idempotency_key="superseded-attempt:retry:start",
            task_id=task_id,
            run_id=retry.run_id,
        )
        lifecycle.complete(retry.run_id, status=ExecutionStatus.SUCCEEDED)
        await kernel.refresh_run(
            idempotency_key="superseded-attempt:retry:refresh",
            task_id=task_id,
            run_id=retry.run_id,
        )

        bridge = ReplanningEvidenceBridge(planning)
        with pytest.raises(ContractError) as exc_info:
            await bridge.from_terminal_run(task_id=task_id, run_id=failed.run_id)
        assert exc_info.value.code is ErrorCode.CONFLICT
        assert exc_info.value.details.get("latest_run_id") == retry.run_id
        assert len(planning.history(task_id)) == 1

    asyncio.run(scenario())


def test_terminal_run_rejects_failure_from_superseded_plan() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agent_repository()
        repository = InMemoryPlanningRepository()
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=PlanningOrchestratorAdapter(repository, fallback=FakeOrchestrator()),
            lifecycle=lifecycle,
            repository=InMemoryKernelRepository(),
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=repository,
            kernel=kernel,
            agents=agents,
        )
        task_id = await _ready_task(kernel, "superseded-plan")
        initial = await planning.propose(
            task_id=task_id,
            idempotency_key="superseded-plan:initial",
        )
        await planning.activate(
            initial.proposal.proposal_id,
            idempotency_key="superseded-plan:activate",
        )
        task = await kernel.get_task(task_id)
        failed = await _failed_step_run(
            kernel,
            lifecycle,
            task_id=task_id,
            step_id=task.step_ids[0],
            key="superseded-plan:failed",
        )

        replacement = await planning.propose(
            task_id=task_id,
            idempotency_key="superseded-plan:replacement",
            trigger=PlanningTrigger.MANUAL,
            reason="authorized replacement before delayed failure delivery",
            evidence_refs=("event_superseded-plan",),
        )
        await planning.activate(
            replacement.proposal.proposal_id,
            idempotency_key="superseded-plan:replacement:activate",
        )
        current = await kernel.get_task(task_id)
        assert current.plan_ref != task.plan_ref

        bridge = ReplanningEvidenceBridge(planning)
        with pytest.raises(ContractError) as exc_info:
            await bridge.from_terminal_run(task_id=task_id, run_id=failed.run_id)
        assert exc_info.value.code is ErrorCode.CONFLICT
        assert exc_info.value.details.get("current_plan_id") == current.plan_ref
        assert len(planning.history(task_id)) == 2

    asyncio.run(scenario())


def test_retry_exhaustion_is_proven_from_canonical_coordination_state() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agent_repository()
        repository = InMemoryPlanningRepository()
        coordinator_repository = InMemoryCoordinatorRepository()
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=PlanningOrchestratorAdapter(repository, fallback=FakeOrchestrator()),
            lifecycle=lifecycle,
            repository=InMemoryKernelRepository(),
        )
        coordinator = DurablePlanStepCoordinator(
            repository=coordinator_repository,
            kernel=kernel,
            coordinator_id="issue-439-evidence",
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=repository,
            kernel=kernel,
            agents=agents,
            coordinator=coordinator,
        )
        task_id = await _ready_task(kernel, "retry-exhausted")
        initial = await planning.propose(
            task_id=task_id,
            idempotency_key="retry-exhausted:initial",
        )
        activated = await planning.activate(
            initial.proposal.proposal_id,
            idempotency_key="retry-exhausted:activate",
        )
        assert activated.activation_plan_id is not None

        task = await kernel.get_task(task_id)
        run_id = task.run_ids[0]
        lifecycle.complete(run_id, status=ExecutionStatus.FAILED)
        await kernel.refresh_run(
            idempotency_key="retry-exhausted:refresh",
            task_id=task_id,
            run_id=run_id,
        )
        await coordinator.observe_run(task_id=task_id, run_id=run_id)
        step_id = task.step_ids[0]

        bridge = ReplanningEvidenceBridge(
            planning,
            coordination_repository=coordinator_repository,
        )
        replacement = await bridge.from_retry_exhaustion(
            task_id=task_id,
            step_id=step_id,
        )
        assert replacement.proposal.trigger is PlanningTrigger.RETRY_EXHAUSTED
        assert replacement.proposal.evidence_refs == (step_id, run_id)

        with pytest.raises(ContractError) as exc_info:
            await bridge.from_retry_exhaustion(
                task_id=task_id,
                step_id=new_id("step"),
            )
        assert exc_info.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_verification_outcomes_are_resolved_from_canonical_audit_history() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agent_repository()
        repository = InMemoryPlanningRepository()
        kernel = PlatformKernel(
            orchestrator=PlanningOrchestratorAdapter(repository, fallback=FakeOrchestrator()),
            lifecycle=FakeLifecycleBackend(),
            repository=InMemoryKernelRepository(),
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=repository,
            kernel=kernel,
            agents=agents,
            replan_policy=ReplanPolicy(max_replans=3),
        )
        task_id = await _ready_task(kernel, "verification")
        initial = await planning.propose(
            task_id=task_id,
            idempotency_key="verification:initial",
        )
        await planning.activate(
            initial.proposal.proposal_id,
            idempotency_key="verification:activate",
        )

        expected = (
            (
                VerificationOutcome.NEEDS_CHANGES,
                PlanningTrigger.VERIFICATION_CHANGES_REQUIRED,
            ),
            (VerificationOutcome.FAIL, PlanningTrigger.VERIFICATION_FAILED),
            (
                VerificationOutcome.INCONCLUSIVE,
                PlanningTrigger.VERIFICATION_INCONCLUSIVE,
            ),
        )
        canonical_events = tuple(
            VerificationAuditEvent(
                event_type=VerificationAuditEventType.RESULT_RECORDED,
                task_id=task_id,
                verification_id=new_id("verification"),
                outcome=outcome,
            )
            for outcome, _trigger in expected
        )
        pass_event = VerificationAuditEvent(
            event_type=VerificationAuditEventType.RESULT_RECORDED,
            task_id=task_id,
            verification_id=new_id("verification"),
            outcome=VerificationOutcome.PASS,
        )
        store = _VerificationEvidenceStore((*canonical_events, pass_event))
        bridge = ReplanningEvidenceBridge(planning, verification_repository=store)

        for canonical, (_outcome, trigger) in zip(canonical_events, expected, strict=True):
            forged = replace(canonical, outcome=VerificationOutcome.PASS)
            proposal = await bridge.from_verification(forged)
            assert proposal.proposal.trigger is trigger
            assert canonical.event_id in proposal.proposal.evidence_refs

        forged_pass = replace(pass_event, outcome=VerificationOutcome.NEEDS_CHANGES)
        with pytest.raises(ContractError) as exc_info:
            await bridge.from_verification(forged_pass)
        assert exc_info.value.code is ErrorCode.CONFLICT

        unknown = VerificationAuditEvent(
            event_type=VerificationAuditEventType.RESULT_RECORDED,
            task_id=task_id,
            verification_id=new_id("verification"),
            outcome=VerificationOutcome.FAIL,
        )
        with pytest.raises(ContractError) as exc_info:
            await bridge.from_verification(unknown)
        assert exc_info.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_replanning_budget_exhaustion_emits_explicit_evidence_event() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agent_repository()
        repository = InMemoryPlanningRepository()
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=PlanningOrchestratorAdapter(repository, fallback=FakeOrchestrator()),
            lifecycle=lifecycle,
            repository=InMemoryKernelRepository(),
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=repository,
            kernel=kernel,
            agents=agents,
            replan_policy=ReplanPolicy(max_replans=1),
        )
        task_id = await _ready_task(kernel, "budget")
        initial = await planning.propose(task_id=task_id, idempotency_key="budget:initial")
        await planning.activate(initial.proposal.proposal_id, idempotency_key="budget:activate")

        events: list[tuple[str, dict[str, JsonValue]]] = []

        def capture(event_type: str, attributes: dict[str, JsonValue]) -> None:
            events.append((event_type, attributes))

        first_event = VerificationAuditEvent(
            event_type=VerificationAuditEventType.RESULT_RECORDED,
            task_id=task_id,
            verification_id=new_id("verification"),
            outcome=VerificationOutcome.NEEDS_CHANGES,
        )
        second_event = VerificationAuditEvent(
            event_type=VerificationAuditEventType.RESULT_RECORDED,
            task_id=task_id,
            verification_id=new_id("verification"),
            outcome=VerificationOutcome.FAIL,
        )
        store = _VerificationEvidenceStore((first_event, second_event))
        bridge = ReplanningEvidenceBridge(
            planning,
            verification_repository=store,
            event_sink=capture,
        )
        await bridge.from_verification(first_event)

        with pytest.raises(ContractError) as exc_info:
            await bridge.from_verification(second_event)
        assert exc_info.value.code is ErrorCode.RESOURCE_EXHAUSTED
        assert any(name == "planning.replan.exhausted" for name, _ in events)

    asyncio.run(scenario())


def test_unexpected_planner_failure_emits_sanitized_replan_failure_event() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agent_repository()
        repository = InMemoryPlanningRepository()
        kernel = PlatformKernel(
            orchestrator=PlanningOrchestratorAdapter(repository, fallback=FakeOrchestrator()),
            lifecycle=FakeLifecycleBackend(),
            repository=InMemoryKernelRepository(),
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=repository,
            kernel=kernel,
            agents=agents,
        )
        task_id = await _ready_task(kernel, "unexpected-failure")
        initial = await planning.propose(
            task_id=task_id,
            idempotency_key="unexpected-failure:initial",
        )
        await planning.activate(
            initial.proposal.proposal_id,
            idempotency_key="unexpected-failure:activate",
        )
        planning.planner = _ExplodingPlanner()

        events: list[tuple[str, dict[str, JsonValue]]] = []

        def capture(event_type: str, attributes: dict[str, JsonValue]) -> None:
            events.append((event_type, attributes))

        bridge = ReplanningEvidenceBridge(planning, event_sink=capture)
        with pytest.raises(TimeoutError, match="sensitive planner backend detail"):
            await bridge.manual(
                task_id=task_id,
                evidence_ref="event_unexpected-planner-failure",
                reason="canonical operator requested replacement planning",
            )

        failure_events = [attributes for name, attributes in events if name == "planning.replan.failed"]
        assert len(failure_events) == 1
        assert failure_events[0].get("error_type") == "TimeoutError"
        assert "sensitive planner backend detail" not in repr(events)

    asyncio.run(scenario())
