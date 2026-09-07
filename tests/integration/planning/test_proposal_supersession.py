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
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, ExecutionStatus
from ai_multi_agent_platform.coordination import (
    DurablePlanStepCoordinator,
    InMemoryCoordinatorRepository,
)
from ai_multi_agent_platform.domain import OwnerRef, TaskStatus
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
    ProposalRecord,
    ProposalStatus,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

OWNER = OwnerRef(type="user", id="issue-439-supersession-user")


def _agents() -> tuple[InMemoryAgentRepository, str, int]:
    repository = InMemoryAgentRepository()
    revision = AgentService(repository).create_agent(
        AgentProfile(
            name="Supersession worker",
            role="worker",
            instructions=AgentInstructions(
                role=InstructionSource(content="Execute one canonical planned Step.", version="1")
            ),
        ),
        owner_ref=OWNER,
    )
    return repository, revision.agent_id, revision.revision


def _draft(agent_id: str, revision: int) -> PlanDraft:
    return PlanDraft(
        summary="Issue 439 supersession plan",
        steps=(
            PlanningStepDraft(
                key="work",
                title="Execute canonical work",
                objective="complete the current plan revision",
                assignment=AgentAssignment(agent_id=agent_id, agent_revision=revision),
            ),
        ),
    )


async def _stack():
    agents, agent_id, revision = _agents()
    repository = InMemoryPlanningRepository()
    lifecycle = FakeLifecycleBackend()
    kernel = PlatformKernel(
        orchestrator=PlanningOrchestratorAdapter(repository, fallback=FakeOrchestrator()),
        lifecycle=lifecycle,
        repository=InMemoryKernelRepository(),
    )
    coordinator = DurablePlanStepCoordinator(
        repository=InMemoryCoordinatorRepository(),
        kernel=kernel,
        coordinator_id="issue-439-supersession",
    )
    planning = PlanningService(
        planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
        repository=repository,
        kernel=kernel,
        agents=agents,
        coordinator=coordinator,
    )
    created = await kernel.create_task(
        idempotency_key="supersession:create",
        title="Issue 439 proposal supersession",
        objective="Prove deterministic replacement proposal activation",
        owner_type="user",
        owner_id=OWNER.id,
    )
    await kernel.ready_task(
        idempotency_key="supersession:ready",
        task_id=created.task_id,
    )
    initial = await planning.propose(
        task_id=created.task_id,
        idempotency_key="supersession:initial",
    )
    activated = await planning.activate(
        initial.proposal.proposal_id,
        idempotency_key="supersession:initial:activate",
    )
    assert activated.status is ProposalStatus.ACTIVATED
    assert activated.activation_plan_id is not None
    return planning, repository, kernel, lifecycle, coordinator, activated


async def _fail_active_step(
    *,
    kernel: PlatformKernel,
    lifecycle: FakeLifecycleBackend,
    coordinator: DurablePlanStepCoordinator,
    task_id: str,
) -> None:
    task = await kernel.get_task(task_id)
    assert task.run_ids
    run_id = task.run_ids[-1]
    lifecycle.complete(run_id, status=ExecutionStatus.FAILED)
    await kernel.refresh_run(
        idempotency_key="supersession:refresh-failed",
        task_id=task_id,
        run_id=run_id,
    )
    await coordinator.observe_run(task_id=task_id, run_id=run_id)
    failed = await kernel.get_task(task_id)
    assert failed.status is TaskStatus.FAILED


def test_competing_replans_have_explicit_lineage_and_exactly_one_activation_winner() -> None:
    async def scenario() -> None:
        planning, repository, kernel, lifecycle, coordinator, initial = await _stack()
        await _fail_active_step(
            kernel=kernel,
            lifecycle=lifecycle,
            coordinator=coordinator,
            task_id=initial.proposal.task_id,
        )

        left = await planning.propose(
            task_id=initial.proposal.task_id,
            idempotency_key="supersession:left",
            trigger=PlanningTrigger.MANUAL,
            reason="candidate replacement left",
            evidence_refs=("event_supersession_left",),
        )
        right = await planning.propose(
            task_id=initial.proposal.task_id,
            idempotency_key="supersession:right",
            trigger=PlanningTrigger.MANUAL,
            reason="candidate replacement right",
            evidence_refs=("event_supersession_right",),
        )
        assert left.proposal.supersedes_proposal_id == initial.proposal.proposal_id
        assert right.proposal.supersedes_proposal_id == initial.proposal.proposal_id

        results = await asyncio.gather(
            planning.activate(
                left.proposal.proposal_id,
                idempotency_key="supersession:left:activate",
            ),
            planning.activate(
                right.proposal.proposal_id,
                idempotency_key="supersession:right:activate",
            ),
            return_exceptions=True,
        )
        winners = [item for item in results if isinstance(item, ProposalRecord)]
        conflicts = [item for item in results if isinstance(item, ContractError)]
        assert len(winners) == 1
        assert winners[0].status is ProposalStatus.ACTIVATED
        assert len(conflicts) == 1
        assert conflicts[0].code is ErrorCode.CONFLICT

        winner_id = winners[0].proposal.proposal_id
        loser_id = (
            right.proposal.proposal_id
            if winner_id == left.proposal.proposal_id
            else left.proposal.proposal_id
        )
        loser = repository.get(loser_id)
        assert loser.status is ProposalStatus.SUPERSEDED
        assert loser.failure_reason == "competing proposal lost canonical activation"

        current = await kernel.get_task(initial.proposal.task_id)
        assert current.plan_ref == winners[0].activation_plan_id
        assert len(lifecycle.start_calls) == 2

        with pytest.raises(ContractError) as exc_info:
            await planning.activate(
                loser_id,
                idempotency_key="supersession:loser:retry",
            )
        assert exc_info.value.code is ErrorCode.CONFLICT
        assert len(lifecycle.start_calls) == 2

    asyncio.run(scenario())


def test_running_predecessor_conflict_keeps_replan_actionable() -> None:
    async def scenario() -> None:
        planning, repository, _kernel, _lifecycle, _coordinator, initial = await _stack()
        replacement = await planning.propose(
            task_id=initial.proposal.task_id,
            idempotency_key="supersession:running",
            trigger=PlanningTrigger.MANUAL,
            reason="replacement must wait for active work",
            evidence_refs=("event_supersession_running",),
        )

        with pytest.raises(ContractError) as exc_info:
            await planning.activate(
                replacement.proposal.proposal_id,
                idempotency_key="supersession:running:activate",
            )
        assert exc_info.value.code is ErrorCode.CONFLICT
        assert "prior Steps are running" in exc_info.value.message
        assert (
            repository.get(replacement.proposal.proposal_id).status is ProposalStatus.VALIDATED
        )

    asyncio.run(scenario())


def test_stale_proposal_becomes_durably_superseded() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agents()
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
        created = await kernel.create_task(
            idempotency_key="supersession:stale:create",
            title="Issue 439 stale proposal",
            objective="Persist deterministic stale proposal disposition",
            owner_type="user",
            owner_id=OWNER.id,
        )
        await kernel.ready_task(
            idempotency_key="supersession:stale:ready",
            task_id=created.task_id,
        )
        proposal = await planning.propose(
            task_id=created.task_id,
            idempotency_key="supersession:stale:propose",
        )
        await kernel.update_task(
            idempotency_key="supersession:stale:mutate",
            task_id=created.task_id,
            metadata={"constraint_revision": 2},
        )

        with pytest.raises(ContractError) as exc_info:
            await planning.activate(
                proposal.proposal.proposal_id,
                idempotency_key="supersession:stale:activate",
            )
        assert exc_info.value.code is ErrorCode.CONFLICT
        stored = repository.get(proposal.proposal.proposal_id)
        assert stored.status is ProposalStatus.SUPERSEDED
        assert stored.failure_reason == "canonical Task/Plan state moved beyond the proposal base"
        assert lifecycle.start_calls == []

    asyncio.run(scenario())
