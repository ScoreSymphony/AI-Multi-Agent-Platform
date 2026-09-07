from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionStatus,
    HealthStatus,
    OperationContext,
)
from ai_multi_agent_platform.coordination import (
    DurablePlanStepCoordinator,
    InMemoryCoordinatorRepository,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.models import (
    DeterministicModelRouter,
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    RoutingRequirements,
)
from ai_multi_agent_platform.planning import (
    AgentAssignment,
    CapabilityRequirement,
    DeterministicReferencePlanner,
    InMemoryPlanningRepository,
    JsonPlanningRepository,
    ModelBackedPlanner,
    PlanDraft,
    PlanningCapabilityCandidate,
    PlanningInventory,
    PlanningOrchestratorAdapter,
    PlanningRequest,
    PlanningService,
    PlanningStepDraft,
    PlanningTrigger,
    ProposalRecord,
    ProposalStatus,
    ProposalValidation,
    ReplanPolicy,
)
from ai_multi_agent_platform.planning.repository import advance_record
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeModelProvider, FakeOrchestrator

OWNER = OwnerRef(type="user", id="issue-439-user")


def _agent_profile() -> AgentProfile:
    return AgentProfile(
        name="Planning worker",
        role="worker",
        instructions=AgentInstructions(
            role=InstructionSource(content="Execute one bounded planned Step.", version="1")
        ),
    )


def _agent_repository() -> tuple[InMemoryAgentRepository, str, int]:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    revision = service.create_agent(_agent_profile(), owner_ref=OWNER)
    return repository, revision.agent_id, revision.revision


def _draft(
    agent_id: str,
    agent_revision: int,
    *,
    steps: tuple[PlanningStepDraft, ...] | None = None,
) -> PlanDraft:
    assignment = AgentAssignment(agent_id=agent_id, agent_revision=agent_revision)
    return PlanDraft(
        summary="Issue 439 reference plan",
        steps=steps
        or (
            PlanningStepDraft(
                key="step-1",
                title="Execute work",
                objective="complete the task",
                assignment=assignment,
            ),
        ),
    )


async def _ready_task(kernel: PlatformKernel, key: str) -> str:
    task = await kernel.create_task(
        idempotency_key=f"{key}:create",
        title="Issue 439",
        objective="Exercise autonomous planning without transferring execution authority",
        owner_type="user",
        owner_id=OWNER.id,
    )
    ready = await kernel.ready_task(
        idempotency_key=f"{key}:ready",
        task_id=task.task_id,
    )
    return ready.task_id


def _stack(
    draft: PlanDraft,
    *,
    repository: InMemoryPlanningRepository | JsonPlanningRepository | None = None,
    coordinator: bool = True,
) -> tuple[
    PlanningService,
    PlatformKernel,
    FakeLifecycleBackend,
    InMemoryPlanningRepository | JsonPlanningRepository,
    InMemoryAgentRepository,
]:
    agents, _, _ = _agent_repository()
    planning_repository = repository or InMemoryPlanningRepository()
    lifecycle = FakeLifecycleBackend()
    kernel = PlatformKernel(
        orchestrator=PlanningOrchestratorAdapter(
            planning_repository,
            fallback=FakeOrchestrator(),
        ),
        lifecycle=lifecycle,
        repository=InMemoryKernelRepository(),
    )
    durable_coordinator = (
        DurablePlanStepCoordinator(
            repository=InMemoryCoordinatorRepository(),
            kernel=kernel,
            coordinator_id="issue-439-test",
        )
        if coordinator
        else None
    )
    planning = PlanningService(
        planner=DeterministicReferencePlanner(draft),
        repository=planning_repository,
        kernel=kernel,
        agents=agents,
        coordinator=durable_coordinator,
    )
    return planning, kernel, lifecycle, planning_repository, agents


def test_linear_parallel_and_diamond_plans_activate_only_through_coordinator() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agent_repository()
        assignment = AgentAssignment(agent_id=agent_id, agent_revision=revision)
        draft = _draft(
            agent_id,
            revision,
            steps=(
                PlanningStepDraft(key="root", title="Root", assignment=assignment),
                PlanningStepDraft(
                    key="left",
                    title="Left",
                    depends_on=("root",),
                    assignment=assignment,
                ),
                PlanningStepDraft(
                    key="right",
                    title="Right",
                    depends_on=("root",),
                    assignment=assignment,
                ),
                PlanningStepDraft(
                    key="join",
                    title="Join",
                    depends_on=("left", "right"),
                    assignment=assignment,
                ),
            ),
        )
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
            coordinator_id="issue-439-diamond",
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(draft),
            repository=repository,
            kernel=kernel,
            agents=agents,
            coordinator=coordinator,
        )
        task_id = await _ready_task(kernel, "diamond")

        proposal = await planning.propose(task_id=task_id, idempotency_key="diamond:propose")
        assert proposal.status is ProposalStatus.VALIDATED
        assert len(lifecycle.start_calls) == 0

        activated = await planning.activate(
            proposal.proposal.proposal_id,
            idempotency_key="diamond:activate",
        )
        assert activated.status is ProposalStatus.ACTIVATED
        assert activated.activation_plan_id is not None
        projection = coordinator.projection(activated.activation_plan_id)
        assert len(projection.steps) == 4
        assert len(lifecycle.start_calls) == 1
        assert lifecycle.start_calls[0].subject_type == "step"

    asyncio.run(scenario())


def test_cycle_and_missing_dependencies_are_rejected_before_activation() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agent_repository()
        assignment = AgentAssignment(agent_id=agent_id, agent_revision=revision)
        draft = _draft(
            agent_id,
            revision,
            steps=(
                PlanningStepDraft(
                    key="a",
                    title="A",
                    depends_on=("b",),
                    assignment=assignment,
                ),
                PlanningStepDraft(
                    key="b",
                    title="B",
                    depends_on=("a",),
                    assignment=assignment,
                ),
                PlanningStepDraft(
                    key="c",
                    title="C",
                    depends_on=("missing",),
                    assignment=assignment,
                ),
            ),
        )
        repository = InMemoryPlanningRepository()
        kernel = PlatformKernel(
            orchestrator=PlanningOrchestratorAdapter(repository, fallback=FakeOrchestrator()),
            lifecycle=FakeLifecycleBackend(),
            repository=InMemoryKernelRepository(),
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(draft),
            repository=repository,
            kernel=kernel,
            agents=agents,
        )
        task_id = await _ready_task(kernel, "invalid-graph")
        proposal = await planning.propose(
            task_id=task_id,
            idempotency_key="invalid-graph:propose",
        )

        assert proposal.status is ProposalStatus.INVALID
        assert any("unknown dependencies" in item for item in proposal.validation.errors)
        with pytest.raises(ContractError) as exc_info:
            await planning.activate(
                proposal.proposal.proposal_id,
                idempotency_key="invalid-graph:activate",
            )
        assert exc_info.value.code is ErrorCode.INVALID_REQUEST

    asyncio.run(scenario())


def test_missing_agent_capability_and_model_are_reported_canonically() -> None:
    async def scenario() -> None:
        agents, _, _ = _agent_repository()
        missing_agent = new_id("agent")
        draft = PlanDraft(
            summary="Unsatisfied proposal",
            steps=(
                PlanningStepDraft(
                    key="unsatisfied",
                    title="Unsatisfied",
                    assignment=AgentAssignment(agent_id=missing_agent, agent_revision=1),
                    capability_requirements=(
                        CapabilityRequirement(capability_id="cap.missing"),
                    ),
                    model_requirements=RoutingRequirements(structured_output=True),
                    requires_model=True,
                ),
            ),
        )
        repository = InMemoryPlanningRepository()
        kernel = PlatformKernel(
            orchestrator=PlanningOrchestratorAdapter(repository, fallback=FakeOrchestrator()),
            lifecycle=FakeLifecycleBackend(),
            repository=InMemoryKernelRepository(),
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(draft),
            repository=repository,
            kernel=kernel,
            agents=agents,
        )
        task_id = await _ready_task(kernel, "unsatisfied")
        proposal = await planning.propose(
            task_id=task_id,
            idempotency_key="unsatisfied:propose",
        )

        joined = "\n".join(proposal.validation.errors)
        assert proposal.status is ProposalStatus.INVALID
        assert "missing Agent revision" in joined
        assert "missing capability" in joined
        assert "no compatible available canonical model" in joined

    asyncio.run(scenario())


def test_provider_private_runtime_identity_is_rejected_from_step_metadata() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agent_repository()
        draft = PlanDraft(
            summary="Provider-private invalid plan",
            steps=(
                PlanningStepDraft(
                    key="private",
                    title="Private",
                    assignment=AgentAssignment(agent_id=agent_id, agent_revision=revision),
                    metadata={"worker_id": "worker_private"},
                ),
            ),
        )
        repository = InMemoryPlanningRepository()
        kernel = PlatformKernel(
            orchestrator=PlanningOrchestratorAdapter(repository, fallback=FakeOrchestrator()),
            lifecycle=FakeLifecycleBackend(),
            repository=InMemoryKernelRepository(),
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(draft),
            repository=repository,
            kernel=kernel,
            agents=agents,
        )
        task_id = await _ready_task(kernel, "private-metadata")
        proposal = await planning.propose(
            task_id=task_id,
            idempotency_key="private-metadata:propose",
        )
        assert proposal.status is ProposalStatus.INVALID
        assert any("provider-private" in item for item in proposal.validation.errors)

    asyncio.run(scenario())


def test_duplicate_trigger_is_idempotent_and_stale_proposal_cannot_activate() -> None:
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
        task_id = await _ready_task(kernel, "duplicate")
        first = await planning.propose(task_id=task_id, idempotency_key="duplicate:first")
        duplicate = await planning.propose(task_id=task_id, idempotency_key="duplicate:second")
        assert duplicate.proposal.proposal_id == first.proposal.proposal_id

        await kernel.update_task(
            idempotency_key="duplicate:mutate",
            task_id=task_id,
            metadata={"constraint_revision": 2},
        )
        with pytest.raises(ContractError) as exc_info:
            await planning.activate(
                first.proposal.proposal_id,
                idempotency_key="duplicate:activate",
            )
        assert exc_info.value.code is ErrorCode.CONFLICT
        assert "stale" in exc_info.value.message
        assert lifecycle.start_calls == []

    asyncio.run(scenario())


def test_privileged_requirement_is_fail_closed_without_approval_authority() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agent_repository()
        repository = InMemoryPlanningRepository()
        kernel = PlatformKernel(
            orchestrator=PlanningOrchestratorAdapter(repository, fallback=FakeOrchestrator()),
            lifecycle=FakeLifecycleBackend(),
            repository=InMemoryKernelRepository(),
        )
        task_id = await _ready_task(kernel, "privileged")
        task = await kernel.get_task(task_id)
        assignment = AgentAssignment(agent_id=agent_id, agent_revision=revision)
        step = PlanningStepDraft(
            key="privileged",
            title="Privileged",
            assignment=assignment,
            capability_requirements=(CapabilityRequirement(capability_id="cap.sensitive"),),
        )
        request = PlanningRequest(
            task_id=task_id,
            task_revision=task.revision,
            objective=task.task.description,
            context=OperationContext(correlation_id=task_id),
            inventory=PlanningInventory(
                agents=(),
                capabilities=(
                    PlanningCapabilityCandidate(
                        capability_id="cap.sensitive",
                        version="1.0",
                        available=True,
                        required_approvals=("human",),
                        safety="sensitive",
                    ),
                ),
            ),
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(),
            repository=repository,
            kernel=kernel,
            agents=agents,
        )
        proposal = await planning.propose(
            task_id=task_id,
            idempotency_key="privileged:propose",
        )
        manual_validation = planning.validate(
            proposal.proposal.__class__(
                proposal_id=proposal.proposal.proposal_id,
                task_id=task_id,
                task_revision=task.revision,
                plan_revision=1,
                trigger=PlanningTrigger.INITIAL,
                summary="Privileged",
                steps=(step,),
                planner=proposal.proposal.planner,
            ),
            request,
        )
        assert manual_validation.approval_required is True

        guarded = ProposalRecord(
            proposal=proposal.proposal.__class__(
                proposal_id=new_id("plan_proposal"),
                task_id=task_id,
                task_revision=task.revision,
                plan_revision=1,
                trigger=PlanningTrigger.INITIAL,
                summary="Guarded",
                steps=(
                    PlanningStepDraft(
                        key="guarded",
                        title="Guarded",
                        assignment=assignment,
                    ),
                ),
                planner=proposal.proposal.planner,
            ),
            status=ProposalStatus.VALIDATED,
            idempotency_key="privileged:guarded",
            validation=ProposalValidation(valid=True, approval_required=True),
            trigger_fingerprint="guarded-trigger",
        )
        repository.create(guarded)
        with pytest.raises(ContractError) as exc_info:
            await planning.activate(
                guarded.proposal.proposal_id,
                idempotency_key="privileged:activate",
            )
        assert exc_info.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())


def test_durable_repository_recovers_activation_gap_and_hands_off_to_coordinator(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agent_repository()
        path = tmp_path / "planning.json"
        repository = JsonPlanningRepository(path)
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
        task_id = await _ready_task(kernel, "restart")
        proposed = await planning.propose(task_id=task_id, idempotency_key="restart:propose")
        activating = advance_record(proposed, status=ProposalStatus.ACTIVATING)
        repository.save(activating, expected_revision=proposed.revision)
        planned = await kernel.plan_task(
            idempotency_key=f"planning:{proposed.proposal.proposal_id}:activate",
            task_id=task_id,
            source="platform-planning",
        )
        assert planned.plan_ref is not None

        restarted_repository = JsonPlanningRepository(path)
        restored = restarted_repository.get(proposed.proposal.proposal_id)
        assert restored.status is ProposalStatus.ACTIVATING
        coordinator = DurablePlanStepCoordinator(
            repository=InMemoryCoordinatorRepository(),
            kernel=kernel,
            coordinator_id="issue-439-restarted",
        )
        restarted = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=restarted_repository,
            kernel=kernel,
            agents=agents,
            coordinator=coordinator,
        )
        recovered = await restarted.activate(
            proposed.proposal.proposal_id,
            idempotency_key="restart:recover",
        )
        assert recovered.status is ProposalStatus.ACTIVATED
        assert recovered.activation_plan_id == planned.plan_ref
        assert coordinator.projection(planned.plan_ref).plan_id == planned.plan_ref
        assert len(lifecycle.start_calls) == 1

    asyncio.run(scenario())


def test_replan_after_partial_success_reuses_completed_work_and_is_bounded() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agent_repository()
        assignment = AgentAssignment(agent_id=agent_id, agent_revision=revision)
        initial = _draft(
            agent_id,
            revision,
            steps=(
                PlanningStepDraft(key="done", title="Done", assignment=assignment),
                PlanningStepDraft(
                    key="later",
                    title="Later",
                    depends_on=("done",),
                    assignment=assignment,
                ),
            ),
        )
        repository = InMemoryPlanningRepository()
        lifecycle = FakeLifecycleBackend()
        kernel = PlatformKernel(
            orchestrator=PlanningOrchestratorAdapter(repository, fallback=FakeOrchestrator()),
            lifecycle=lifecycle,
            repository=InMemoryKernelRepository(),
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(initial),
            repository=repository,
            kernel=kernel,
            agents=agents,
        )
        task_id = await _ready_task(kernel, "partial")
        proposed = await planning.propose(task_id=task_id, idempotency_key="partial:propose")
        await planning.activate(proposed.proposal.proposal_id, idempotency_key="partial:activate")
        current = await kernel.get_task(task_id)
        completed_step_id = current.step_ids[0]
        run = await kernel.create_run(
            idempotency_key="partial:create-run",
            task_id=task_id,
            subject_type="step",
            subject_id=completed_step_id,
        )
        await kernel.start_run(
            idempotency_key="partial:start-run",
            task_id=task_id,
            run_id=run.run_id,
        )
        lifecycle.complete(run.run_id, status=ExecutionStatus.SUCCEEDED)
        await kernel.refresh_run(
            idempotency_key="partial:refresh-run",
            task_id=task_id,
            run_id=run.run_id,
        )

        replan_draft = PlanDraft(
            summary="Reuse completed work",
            steps=(
                PlanningStepDraft(
                    key="replacement",
                    title="Replacement",
                    assignment=assignment,
                    reuse_step_ids=(completed_step_id,),
                ),
            ),
        )
        replanning = PlanningService(
            planner=DeterministicReferencePlanner(replan_draft),
            repository=repository,
            kernel=kernel,
            agents=agents,
            replan_policy=ReplanPolicy(max_replans=1),
        )
        replacement = await replanning.propose(
            task_id=task_id,
            idempotency_key="partial:replan-1",
            trigger=PlanningTrigger.TERMINAL_FAILURE,
            reason="remaining work failed",
        )
        assert replacement.status is ProposalStatus.VALIDATED
        assert replacement.proposal.steps[0].reuse_step_ids == (completed_step_id,)

        with pytest.raises(ContractError) as exc_info:
            await replanning.propose(
                task_id=task_id,
                idempotency_key="partial:replan-2",
                trigger=PlanningTrigger.MANUAL,
                reason="second replan exceeds budget",
            )
        assert exc_info.value.code is ErrorCode.RESOURCE_EXHAUSTED

    asyncio.run(scenario())


def test_model_backed_planner_routes_via_canonical_self_hosted_model() -> None:
    async def scenario() -> None:
        provider = FakeModelProvider(
            response_text=json.dumps(
                {
                    "summary": "Model proposed plan",
                    "steps": [
                        {
                            "key": "step-1",
                            "title": "Model step",
                            "assignment": {"role_requirement": "worker"},
                        }
                    ],
                }
            )
        )
        registry = ModelRegistry()
        registry.register_provider(provider)
        registry.register_model(
            ModelConfiguration(
                config_id="planning-self-hosted",
                display_name="Planning self-hosted",
                provider_id=provider.descriptor.provider_id,
                capabilities=ModelCapabilities(structured_output=True),
                location=ModelLocation.SELF_HOSTED,
                health=HealthStatus.HEALTHY,
            )
        )
        planner = ModelBackedPlanner(
            router=DeterministicModelRouter(registry),
            registry=registry,
        )
        task_id = new_id("task")
        output = await planner.propose(
            PlanningRequest(
                task_id=task_id,
                task_revision=1,
                objective="Create a provider-neutral plan",
                context=OperationContext(correlation_id=task_id),
                inventory=PlanningInventory(),
            )
        )
        assert output.model_config_id == "planning-self-hosted"
        assert output.draft.summary == "Model proposed plan"
        assert output.draft.steps[0].assignment is not None
        assert output.draft.steps[0].assignment.role_requirement == "worker"
        assert "fake-model/default" not in repr(output.draft)

    asyncio.run(scenario())
