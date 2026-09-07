from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

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
    JsonPlanningRepository,
    PlanDraft,
    PlanningOrchestratorAdapter,
    PlanningService,
    PlanningStepDraft,
    PlanningTrigger,
    ProposalStatus,
)
from ai_multi_agent_platform.planning.repository import advance_record
from ai_multi_agent_platform.security import AuthorizationGate, LocalAuthorizationProvider
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

OWNER = OwnerRef(type="user", id="issue-439-failed-replan-user")


def _agents() -> tuple[InMemoryAgentRepository, str, int]:
    repository = InMemoryAgentRepository()
    revision = AgentService(repository).create_agent(
        AgentProfile(
            name="Failed replan worker",
            role="worker",
            instructions=AgentInstructions(
                role=InstructionSource(content="Execute one canonical Step.", version="1")
            ),
        ),
        owner_ref=OWNER,
    )
    return repository, revision.agent_id, revision.revision


def _draft(agent_id: str, revision: int) -> PlanDraft:
    return PlanDraft(
        summary="Issue 439 failed-task replacement plan",
        steps=(
            PlanningStepDraft(
                key="work",
                title="Execute replacement work",
                objective="complete canonical work",
                assignment=AgentAssignment(agent_id=agent_id, agent_revision=revision),
            ),
        ),
    )


async def _failed_task(
    *,
    planning_repository: InMemoryPlanningRepository | JsonPlanningRepository,
):
    agents, agent_id, revision = _agents()
    lifecycle = FakeLifecycleBackend()
    kernel = PlatformKernel(
        orchestrator=PlanningOrchestratorAdapter(
            planning_repository,
            fallback=FakeOrchestrator(),
        ),
        lifecycle=lifecycle,
        repository=InMemoryKernelRepository(),
    )
    coordinator_repository = InMemoryCoordinatorRepository()
    coordinator = DurablePlanStepCoordinator(
        repository=coordinator_repository,
        kernel=kernel,
        coordinator_id="issue-439-failed-replan",
    )
    planning = PlanningService(
        planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
        repository=planning_repository,
        kernel=kernel,
        agents=agents,
        coordinator=coordinator,
    )
    created = await kernel.create_task(
        idempotency_key="failed-replan:create",
        title="Issue 439 failed replan",
        objective="Replace a failed canonical Plan without bypassing lifecycle authority",
        owner_type="user",
        owner_id=OWNER.id,
    )
    await kernel.ready_task(
        idempotency_key="failed-replan:ready",
        task_id=created.task_id,
    )
    initial = await planning.propose(
        task_id=created.task_id,
        idempotency_key="failed-replan:initial",
    )
    activated = await planning.activate(
        initial.proposal.proposal_id,
        idempotency_key="failed-replan:initial:activate",
    )
    assert activated.activation_plan_id is not None

    running = await kernel.get_task(created.task_id)
    assert running.run_ids
    failed_run_id = running.run_ids[-1]
    lifecycle.complete(failed_run_id, status=ExecutionStatus.FAILED)
    await kernel.refresh_run(
        idempotency_key="failed-replan:refresh",
        task_id=created.task_id,
        run_id=failed_run_id,
    )
    await coordinator.observe_run(task_id=created.task_id, run_id=failed_run_id)
    failed = await kernel.get_task(created.task_id)
    assert failed.status is TaskStatus.FAILED
    assert failed.plan_ref == activated.activation_plan_id
    return (
        planning,
        kernel,
        lifecycle,
        agents,
        coordinator,
        coordinator_repository,
        agent_id,
        revision,
        failed,
    )


def _planning_metadata(event) -> Mapping[str, object] | None:
    raw = event.payload.get("adapter_metadata")
    if not isinstance(raw, Mapping):
        return None
    value = raw.get("platform-planning")
    return value if isinstance(value, Mapping) else None


def test_failed_replan_commits_plan_then_readies_before_coordinator_dispatch() -> None:
    async def scenario() -> None:
        repository = InMemoryPlanningRepository()
        planning, kernel, _lifecycle, _agents_repo, _coordinator, _coord_repo, *_rest = (
            await _failed_task(planning_repository=repository)
        )
        failed = _rest[-1]
        prior_plan_id = failed.plan_ref
        assert prior_plan_id is not None

        replacement = await planning.propose(
            task_id=failed.task_id,
            idempotency_key="failed-replan:replacement",
            trigger=PlanningTrigger.MANUAL,
            reason="replace terminal failed work",
            evidence_refs=("event_failed-replan",),
        )
        activated = await planning.activate(
            replacement.proposal.proposal_id,
            idempotency_key="failed-replan:replacement:activate",
        )
        assert activated.status is ProposalStatus.ACTIVATED
        assert activated.activation_plan_id is not None
        assert activated.activation_plan_id != prior_plan_id

        history = await kernel.history(failed.task_id)
        replacement_plan = next(
            event
            for event in history
            if event.event_type == "plan.created"
            and (_planning_metadata(event) or {}).get("proposal_id")
            == replacement.proposal.proposal_id
        )
        ready_key = f"planning:{replacement.proposal.proposal_id}:ready-for-handoff"
        ready_event = next(
            event
            for event in history
            if event.event_type == "task.ready" and event.causation_id == ready_key
        )
        replacement_run = next(
            event
            for event in history
            if event.event_type == "run.created"
            and event.payload.get("plan_ref") == activated.activation_plan_id
        )
        assert history.index(replacement_plan) < history.index(ready_event) < history.index(
            replacement_run
        )
        assert ready_event.provenance is not None
        assert ready_event.provenance.source == "platform-planning"
        assert ready_event.provenance.actor_ref == replacement_plan.provenance.actor_ref
        assert (await kernel.get_task(failed.task_id)).status is TaskStatus.RUNNING

    asyncio.run(scenario())


def test_restart_after_replan_plan_commit_readies_and_hands_off_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "planning.json"
        repository = JsonPlanningRepository(path)
        (
            planning,
            kernel,
            lifecycle,
            agents,
            _coordinator,
            coordinator_repository,
            agent_id,
            revision,
            failed,
        ) = await _failed_task(planning_repository=repository)
        replacement = await planning.propose(
            task_id=failed.task_id,
            idempotency_key="restart-replan:replacement",
            trigger=PlanningTrigger.MANUAL,
            reason="prove restart-safe failed replan activation",
            evidence_refs=("event_restart-replan",),
        )
        activating = advance_record(replacement, status=ProposalStatus.ACTIVATING)
        repository.save(activating, expected_revision=replacement.revision)

        planned = await kernel.plan_task(
            idempotency_key=f"planning:{replacement.proposal.proposal_id}:activate",
            task_id=failed.task_id,
            actor_ref=f"user:{OWNER.id}",
            source="platform-planning",
        )
        assert planned.status is TaskStatus.FAILED
        assert planned.plan_ref is not None
        assert planned.plan_ref != replacement.proposal.base_plan_id
        assert lifecycle.start_calls[-1].run_id != planned.plan_ref

        restarted_repository = JsonPlanningRepository(path)
        restarted_coordinator = DurablePlanStepCoordinator(
            repository=coordinator_repository,
            kernel=kernel,
            coordinator_id="issue-439-failed-replan-restarted",
        )
        restarted = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=restarted_repository,
            kernel=kernel,
            agents=agents,
            coordinator=restarted_coordinator,
        )
        recovered = await restarted.activate(
            replacement.proposal.proposal_id,
            idempotency_key="restart-replan:recover",
        )
        duplicate = await restarted.activate(
            replacement.proposal.proposal_id,
            idempotency_key="restart-replan:recover-again",
        )
        assert recovered.status is ProposalStatus.ACTIVATED
        assert duplicate.activation_plan_id == recovered.activation_plan_id == planned.plan_ref

        history = await kernel.history(failed.task_id)
        ready_key = f"planning:{replacement.proposal.proposal_id}:ready-for-handoff"
        assert sum(
            event.event_type == "task.ready" and event.causation_id == ready_key
            for event in history
        ) == 1
        assert sum(
            event.event_type == "run.created" and event.payload.get("plan_ref") == planned.plan_ref
            for event in history
        ) == 1

    asyncio.run(scenario())


def test_denied_failed_replan_activation_does_not_mutate_plan_or_task_lifecycle() -> None:
    async def scenario() -> None:
        repository = InMemoryPlanningRepository()
        (
            _planning,
            kernel,
            _lifecycle,
            agents,
            coordinator,
            _coord_repo,
            agent_id,
            revision,
            failed,
        ) = await _failed_task(planning_repository=repository)
        guarded = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=repository,
            kernel=kernel,
            agents=agents,
            coordinator=coordinator,
            authorization=AuthorizationGate(LocalAuthorizationProvider(())),
        )
        replacement = await guarded.propose(
            task_id=failed.task_id,
            idempotency_key="denied-replan:replacement",
            trigger=PlanningTrigger.MANUAL,
            reason="authorization must precede failed-task reactivation",
            evidence_refs=("event_denied-replan",),
        )
        before = await kernel.history(failed.task_id)

        with pytest.raises(ContractError) as exc_info:
            await guarded.activate(
                replacement.proposal.proposal_id,
                idempotency_key="denied-replan:activate",
            )
        assert exc_info.value.code is ErrorCode.FORBIDDEN
        after = await kernel.history(failed.task_id)
        assert after == before
        assert (await kernel.get_task(failed.task_id)).status is TaskStatus.FAILED
        assert repository.get(replacement.proposal.proposal_id).status is ProposalStatus.VALIDATED

    asyncio.run(scenario())


def test_existing_plan_event_cannot_bypass_proposal_activation_state() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agents()
        activation_repository = InMemoryPlanningRepository()
        kernel = PlatformKernel(
            orchestrator=PlanningOrchestratorAdapter(
                activation_repository,
                fallback=FakeOrchestrator(),
            ),
            lifecycle=FakeLifecycleBackend(),
            repository=InMemoryKernelRepository(),
        )
        created = await kernel.create_task(
            idempotency_key="state-guard:create",
            title="Issue 439 activation state guard",
            objective="Do not treat canonical Plan presence as authorization proof",
            owner_type="user",
            owner_id=OWNER.id,
        )
        await kernel.ready_task(idempotency_key="state-guard:ready", task_id=created.task_id)
        planning = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=activation_repository,
            kernel=kernel,
            agents=agents,
        )
        proposal = await planning.propose(
            task_id=created.task_id,
            idempotency_key="state-guard:proposal",
        )
        activation_repository.save(
            advance_record(proposal, status=ProposalStatus.ACTIVATING),
            expected_revision=proposal.revision,
        )
        await kernel.plan_task(
            idempotency_key=f"planning:{proposal.proposal.proposal_id}:activate",
            task_id=created.task_id,
            source="platform-planning",
        )

        unactivated_repository = InMemoryPlanningRepository()
        unactivated_repository.create(proposal)
        guarded = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=unactivated_repository,
            kernel=kernel,
            agents=agents,
        )
        with pytest.raises(ContractError) as exc_info:
            await guarded.activate(
                proposal.proposal.proposal_id,
                idempotency_key="state-guard:recover",
            )
        assert exc_info.value.code is ErrorCode.CONFLICT
        assert "never entered authorized activation" in exc_info.value.message
        assert unactivated_repository.get(proposal.proposal.proposal_id).status is ProposalStatus.VALIDATED

    asyncio.run(scenario())
