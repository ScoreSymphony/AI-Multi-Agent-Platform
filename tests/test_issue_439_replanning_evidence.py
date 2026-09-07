from __future__ import annotations

import asyncio

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
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.planning import (
    AgentAssignment,
    DeterministicReferencePlanner,
    InMemoryPlanningRepository,
    PlanDraft,
    PlanningOrchestratorAdapter,
    PlanningService,
    PlanningStepDraft,
    PlanningTrigger,
    ReplanPolicy,
    ReplanningEvidenceBridge,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.verification import VerificationOutcome
from ai_multi_agent_platform.verification.audit import (
    VerificationAuditEvent,
    VerificationAuditEventType,
)

OWNER = OwnerRef(type="user", id="issue-439-evidence-user")


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
        run = await kernel.create_run(
            idempotency_key="run-failure:create-run",
            task_id=task_id,
            subject_type="step",
            subject_id=task.step_ids[0],
        )
        await kernel.start_run(
            idempotency_key="run-failure:start-run",
            task_id=task_id,
            run_id=run.run_id,
        )
        lifecycle.complete(run.run_id, status=ExecutionStatus.FAILED)
        await kernel.refresh_run(
            idempotency_key="run-failure:refresh-run",
            task_id=task_id,
            run_id=run.run_id,
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


def test_verification_outcomes_map_to_canonical_replan_triggers() -> None:
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
        bridge = ReplanningEvidenceBridge(planning)

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
        for outcome, trigger in expected:
            event = VerificationAuditEvent(
                event_type=VerificationAuditEventType.RESULT_RECORDED,
                task_id=task_id,
                verification_id=new_id("verification"),
                outcome=outcome,
            )
            proposal = await bridge.from_verification(event)
            assert proposal.proposal.trigger is trigger
            assert event.event_id in proposal.proposal.evidence_refs

        pass_event = VerificationAuditEvent(
            event_type=VerificationAuditEventType.RESULT_RECORDED,
            task_id=task_id,
            verification_id=new_id("verification"),
            outcome=VerificationOutcome.PASS,
        )
        with pytest.raises(ContractError) as exc_info:
            await bridge.from_verification(pass_event)
        assert exc_info.value.code is ErrorCode.CONFLICT

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

        bridge = ReplanningEvidenceBridge(planning, event_sink=capture)
        first_event = VerificationAuditEvent(
            event_type=VerificationAuditEventType.RESULT_RECORDED,
            task_id=task_id,
            verification_id=new_id("verification"),
            outcome=VerificationOutcome.NEEDS_CHANGES,
        )
        await bridge.from_verification(first_event)

        second_event = VerificationAuditEvent(
            event_type=VerificationAuditEventType.RESULT_RECORDED,
            task_id=task_id,
            verification_id=new_id("verification"),
            outcome=VerificationOutcome.FAIL,
        )
        with pytest.raises(ContractError) as exc_info:
            await bridge.from_verification(second_event)
        assert exc_info.value.code is ErrorCode.RESOURCE_EXHAUSTED
        assert any(name == "planning.replan.exhausted" for name, _ in events)

    asyncio.run(scenario())
