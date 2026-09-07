"""Deterministic #19 evaluation executor for the platform-owned #439 planning contract.

This module is intentionally a reference/contract evaluator, not a model-quality benchmark.  It
runs planner scenarios entirely through local platform components and projects structured evidence
that the canonical deterministic evaluator can score.  No paid model/API service is required and
planning never gains execution authority as a side effect of evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.planning import (
    AgentAssignment,
    CapabilityRequirement,
    DeterministicReferencePlanner,
    InMemoryPlanningRepository,
    PlanDraft,
    PlanningAgentCandidate,
    PlanningCapabilityCandidate,
    PlanningInventory,
    PlanningOnlyLifecycleBackend,
    PlanningOrchestratorAdapter,
    PlanningRequest,
    PlanningService,
    PlanningStepDraft,
    PlanningTrigger,
    PlanProposal,
    ProposalRecord,
    ProposalStatus,
    ReplanPolicy,
)

from .context import EvaluationExecutionContext
from .models import EvaluationAttempt, EvaluationCase, EvaluationObservation

_OWNER = OwnerRef(type="service", id="planning-evaluation")
_SUPPORTED_SCENARIOS = frozenset(
    {
        "dependency_parallelism",
        "unavailable_requirement",
        "provider_replacement",
        "duplicate_evidence",
        "bounded_replanning",
        "no_privilege_escalation",
    }
)


@dataclass(frozen=True, slots=True)
class _ScenarioResult:
    data: dict[str, JsonValue]
    metrics: dict[str, float]
    task_id: str | None = None


class ReferencePlanningEvaluationCaseExecutor:
    """Execute versioned deterministic planning scenarios using only platform-owned contracts."""

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        if execution_context.attempt_id != attempt.attempt_id:
            raise ValueError("evaluation execution context belongs to another attempt")
        scenario = case.input_template.get("scenario")
        if not isinstance(scenario, str) or scenario not in _SUPPORTED_SCENARIOS:
            raise ValueError(
                "planning evaluation case requires supported input_template.scenario; "
                f"got {scenario!r}"
            )

        if scenario == "dependency_parallelism":
            result = await self._dependency_parallelism(attempt)
        elif scenario == "unavailable_requirement":
            result = await self._unavailable_requirement(attempt)
        elif scenario == "provider_replacement":
            result = await self._provider_replacement(attempt)
        elif scenario == "duplicate_evidence":
            result = await self._duplicate_evidence(attempt)
        elif scenario == "bounded_replanning":
            result = await self._bounded_replanning(attempt)
        else:
            result = await self._no_privilege_escalation(attempt)

        return EvaluationObservation(
            data={"scenario": scenario, **result.data},
            metrics=result.metrics,
            task_id=result.task_id,
        )

    async def _dependency_parallelism(self, attempt: EvaluationAttempt) -> _ScenarioResult:
        agents, agent_id, revision = _agent_repository()
        assignment = AgentAssignment(agent_id=agent_id, agent_revision=revision)
        draft = PlanDraft(
            summary="Dependency and safe-parallelism reference plan",
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
        planning, kernel, _ = _stack(agents=agents, draft=draft)
        task_id = await _ready_task(kernel, f"{attempt.attempt_id}:dependency")
        record = await planning.propose(
            task_id=task_id,
            idempotency_key=f"{attempt.attempt_id}:dependency:propose",
            max_parallel_steps=2,
        )
        layers = _topological_layers(record.proposal.steps)
        peak = max((len(layer) for layer in layers), default=0)
        return _ScenarioResult(
            data={
                "status": record.status.value,
                "validation_valid": record.validation.valid,
                "steps": _step_contract(record.proposal.steps),
                "parallel_layers": [list(layer) for layer in layers],
                "peak_parallelism": peak,
            },
            metrics={
                "peak_parallelism": float(peak),
                "step_count": float(len(record.proposal.steps)),
            },
            task_id=task_id,
        )

    async def _unavailable_requirement(self, attempt: EvaluationAttempt) -> _ScenarioResult:
        agents, agent_id, revision = _agent_repository()
        assignment = AgentAssignment(agent_id=agent_id, agent_revision=revision)
        step = PlanningStepDraft(
            key="blocked",
            title="Unavailable capability",
            assignment=assignment,
            capability_requirements=(CapabilityRequirement(capability_id="cap.unavailable"),),
        )
        planning, kernel, _ = _stack(
            agents=agents,
            draft=PlanDraft(summary="Unavailable requirement", steps=(step,)),
        )
        task_id = await _ready_task(kernel, f"{attempt.attempt_id}:unavailable")
        task = await kernel.get_task(task_id)
        request = PlanningRequest(
            task_id=task_id,
            task_revision=task.revision,
            objective=task.task.description,
            context=OperationContext(correlation_id=task_id),
            inventory=PlanningInventory(
                agents=(
                    PlanningAgentCandidate(
                        agent_id=agent_id,
                        revision=revision,
                        role="worker",
                    ),
                ),
                capabilities=(
                    PlanningCapabilityCandidate(
                        capability_id="cap.unavailable",
                        version="1.0",
                        available=False,
                    ),
                ),
            ),
        )
        proposal = PlanProposal(
            proposal_id=new_id("plan_proposal"),
            task_id=task_id,
            task_revision=task.revision,
            plan_revision=1,
            trigger=PlanningTrigger.INITIAL,
            summary="Unavailable requirement",
            steps=(step,),
            planner=planning.planner.descriptor,
        )
        validation = planning.validate(proposal, request)
        reported = any("unavailable capability" in error for error in validation.errors)
        return _ScenarioResult(
            data={
                "validation_valid": validation.valid,
                "unavailable_requirement_reported": reported,
                "errors": list(validation.errors),
                "approval_required": validation.approval_required,
            },
            metrics={"validation_error_count": float(len(validation.errors))},
            task_id=task_id,
        )

    async def _provider_replacement(self, attempt: EvaluationAttempt) -> _ScenarioResult:
        agents, agent_id, revision = _agent_repository()
        assignment = AgentAssignment(agent_id=agent_id, agent_revision=revision)
        draft = PlanDraft(
            summary="Provider-neutral replacement contract",
            steps=(
                PlanningStepDraft(key="inspect", title="Inspect", assignment=assignment),
                PlanningStepDraft(
                    key="finish",
                    title="Finish",
                    depends_on=("inspect",),
                    assignment=assignment,
                ),
            ),
        )
        planning_a, kernel_a, _ = _stack(
            agents=agents,
            draft=draft,
            planner_id="reference-planner-a",
        )
        planning_b, kernel_b, _ = _stack(
            agents=agents,
            draft=draft,
            planner_id="reference-planner-b",
        )
        task_a = await _ready_task(kernel_a, f"{attempt.attempt_id}:provider:a")
        task_b = await _ready_task(kernel_b, f"{attempt.attempt_id}:provider:b")
        first = await planning_a.propose(
            task_id=task_a,
            idempotency_key=f"{attempt.attempt_id}:provider:a:propose",
        )
        second = await planning_b.propose(
            task_id=task_b,
            idempotency_key=f"{attempt.attempt_id}:provider:b:propose",
        )
        first_contract = _step_contract(first.proposal.steps)
        second_contract = _step_contract(second.proposal.steps)
        return _ScenarioResult(
            data={
                "first_status": first.status.value,
                "second_status": second.status.value,
                "planner_ids_distinct": (
                    first.proposal.planner.planner_id != second.proposal.planner.planner_id
                ),
                "same_canonical_contract": first_contract == second_contract,
                "first_contract": first_contract,
                "second_contract": second_contract,
            },
            metrics={"contract_step_count": float(len(first.proposal.steps))},
            task_id=task_a,
        )

    async def _duplicate_evidence(self, attempt: EvaluationAttempt) -> _ScenarioResult:
        agents, agent_id, revision = _agent_repository()
        assignment = AgentAssignment(agent_id=agent_id, agent_revision=revision)
        initial = PlanDraft(
            summary="Initial plan",
            steps=(PlanningStepDraft(key="initial", title="Initial", assignment=assignment),),
        )
        repository = InMemoryPlanningRepository()
        kernel = _kernel(repository)
        planning = PlanningService(
            planner=DeterministicReferencePlanner(initial),
            repository=repository,
            kernel=kernel,
            agents=agents,
        )
        task_id = await _ready_task(kernel, f"{attempt.attempt_id}:duplicate")
        initial_record = await planning.propose(
            task_id=task_id,
            idempotency_key=f"{attempt.attempt_id}:duplicate:initial",
        )
        await planning.activate(
            initial_record.proposal.proposal_id,
            idempotency_key=f"{attempt.attempt_id}:duplicate:activate",
        )

        replacement = PlanDraft(
            summary="Replacement plan",
            steps=(
                PlanningStepDraft(key="replacement", title="Replacement", assignment=assignment),
            ),
        )
        replanning = PlanningService(
            planner=DeterministicReferencePlanner(replacement),
            repository=repository,
            kernel=kernel,
            agents=agents,
            replan_policy=ReplanPolicy(max_replans=2),
        )
        first = await replanning.propose(
            task_id=task_id,
            idempotency_key=f"{attempt.attempt_id}:duplicate:first",
            trigger=PlanningTrigger.TERMINAL_FAILURE,
            reason="canonical terminal failure",
            evidence_refs=("evidence:terminal-failure",),
        )
        duplicate = await replanning.propose(
            task_id=task_id,
            idempotency_key=f"{attempt.attempt_id}:duplicate:second",
            trigger=PlanningTrigger.TERMINAL_FAILURE,
            reason="canonical terminal failure",
            evidence_refs=("evidence:terminal-failure",),
        )
        history = repository.list_for_task(task_id)
        return _ScenarioResult(
            data={
                "same_proposal": first.proposal.proposal_id == duplicate.proposal.proposal_id,
                "same_digest": first.proposal.digest == duplicate.proposal.digest,
                "proposal_count": len(history),
                "replan_status": first.status.value,
            },
            metrics={"proposal_count": float(len(history)), "deduplicated_signals": 1.0},
            task_id=task_id,
        )

    async def _bounded_replanning(self, attempt: EvaluationAttempt) -> _ScenarioResult:
        agents, agent_id, revision = _agent_repository()
        assignment = AgentAssignment(agent_id=agent_id, agent_revision=revision)
        initial = PlanDraft(
            summary="Bounded initial plan",
            steps=(PlanningStepDraft(key="initial", title="Initial", assignment=assignment),),
        )
        repository = InMemoryPlanningRepository()
        kernel = _kernel(repository)
        planning = PlanningService(
            planner=DeterministicReferencePlanner(initial),
            repository=repository,
            kernel=kernel,
            agents=agents,
        )
        task_id = await _ready_task(kernel, f"{attempt.attempt_id}:budget")
        initial_record = await planning.propose(
            task_id=task_id,
            idempotency_key=f"{attempt.attempt_id}:budget:initial",
        )
        await planning.activate(
            initial_record.proposal.proposal_id,
            idempotency_key=f"{attempt.attempt_id}:budget:activate",
        )

        replacement = PlanDraft(
            summary="Bounded replacement",
            steps=(
                PlanningStepDraft(key="replacement", title="Replacement", assignment=assignment),
            ),
        )
        replanning = PlanningService(
            planner=DeterministicReferencePlanner(replacement),
            repository=repository,
            kernel=kernel,
            agents=agents,
            replan_policy=ReplanPolicy(max_replans=1),
        )
        first = await replanning.propose(
            task_id=task_id,
            idempotency_key=f"{attempt.attempt_id}:budget:first",
            trigger=PlanningTrigger.TERMINAL_FAILURE,
            reason="first canonical failure",
            evidence_refs=("evidence:first",),
        )
        error_code: str | None = None
        try:
            await replanning.propose(
                task_id=task_id,
                idempotency_key=f"{attempt.attempt_id}:budget:second",
                trigger=PlanningTrigger.RETRY_EXHAUSTED,
                reason="retry budget exhausted",
                evidence_refs=("evidence:second",),
            )
        except ContractError as exc:
            error_code = exc.code.value
        return _ScenarioResult(
            data={
                "first_replan_valid": first.validation.valid,
                "first_replan_status": first.status.value,
                "budget_error_code": error_code,
                "budget_exhausted": error_code == ErrorCode.RESOURCE_EXHAUSTED.value,
            },
            metrics={"accepted_replans": 1.0},
            task_id=task_id,
        )

    async def _no_privilege_escalation(self, attempt: EvaluationAttempt) -> _ScenarioResult:
        agents, agent_id, revision = _agent_repository()
        assignment = AgentAssignment(agent_id=agent_id, agent_revision=revision)
        step = PlanningStepDraft(
            key="sensitive",
            title="Sensitive capability",
            assignment=assignment,
            capability_requirements=(CapabilityRequirement(capability_id="cap.sensitive"),),
        )
        repository = InMemoryPlanningRepository()
        kernel = _kernel(repository)
        planning = PlanningService(
            planner=DeterministicReferencePlanner(
                PlanDraft(summary="Sensitive plan", steps=(step,))
            ),
            repository=repository,
            kernel=kernel,
            agents=agents,
        )
        task_id = await _ready_task(kernel, f"{attempt.attempt_id}:privilege")
        task = await kernel.get_task(task_id)
        request = PlanningRequest(
            task_id=task_id,
            task_revision=task.revision,
            objective=task.task.description,
            context=OperationContext(correlation_id=task_id),
            inventory=PlanningInventory(
                agents=(
                    PlanningAgentCandidate(
                        agent_id=agent_id,
                        revision=revision,
                        role="worker",
                    ),
                ),
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
        proposal = PlanProposal(
            proposal_id=new_id("plan_proposal"),
            task_id=task_id,
            task_revision=task.revision,
            plan_revision=1,
            trigger=PlanningTrigger.INITIAL,
            summary="Sensitive plan",
            steps=(step,),
            planner=planning.planner.descriptor,
        )
        validation = planning.validate(proposal, request)
        record = ProposalRecord(
            proposal=proposal,
            status=ProposalStatus.VALIDATED,
            idempotency_key=f"{attempt.attempt_id}:privilege:proposal",
            validation=validation,
            trigger_fingerprint=f"{attempt.attempt_id}:privilege:fingerprint",
        )
        repository.create(record)

        error_code: str | None = None
        try:
            await planning.activate(
                proposal.proposal_id,
                idempotency_key=f"{attempt.attempt_id}:privilege:activate",
            )
        except ContractError as exc:
            error_code = exc.code.value
        after = await kernel.get_task(task_id)
        history = await kernel.history(task_id)
        plan_created = any(event.event_type == "plan.created" for event in history)
        return _ScenarioResult(
            data={
                "validation_valid": validation.valid,
                "approval_required": validation.approval_required,
                "activation_error_code": error_code,
                "plan_created": plan_created,
                "run_count": len(after.run_ids),
            },
            metrics={"run_count": float(len(after.run_ids))},
            task_id=task_id,
        )


def _agent_repository() -> tuple[InMemoryAgentRepository, str, int]:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    revision = service.create_agent(
        AgentProfile(
            name="Planning evaluation worker",
            role="worker",
            instructions=AgentInstructions(
                role=InstructionSource(
                    content="Execute one bounded deterministic planning evaluation Step.",
                    version="1",
                )
            ),
        ),
        owner_ref=_OWNER,
    )
    return repository, revision.agent_id, revision.revision


def _kernel(repository: InMemoryPlanningRepository) -> PlatformKernel:
    return PlatformKernel(
        orchestrator=PlanningOrchestratorAdapter(
            repository,
            fallback=ReferenceOrchestrator(),
        ),
        lifecycle=PlanningOnlyLifecycleBackend(),
        repository=InMemoryKernelRepository(),
    )


def _stack(
    *,
    agents: InMemoryAgentRepository,
    draft: PlanDraft,
    planner_id: str = "reference-planner",
) -> tuple[PlanningService, PlatformKernel, InMemoryPlanningRepository]:
    repository = InMemoryPlanningRepository()
    kernel = _kernel(repository)
    planning = PlanningService(
        planner=DeterministicReferencePlanner(draft, planner_id=planner_id),
        repository=repository,
        kernel=kernel,
        agents=agents,
    )
    return planning, kernel, repository


async def _ready_task(kernel: PlatformKernel, key: str) -> str:
    task = await kernel.create_task(
        idempotency_key=f"{key}:create",
        title="Planning evaluation",
        objective="Evaluate the canonical autonomous planning contract deterministically",
        owner_type="service",
        owner_id=_OWNER.id,
    )
    ready = await kernel.ready_task(
        idempotency_key=f"{key}:ready",
        task_id=task.task_id,
    )
    return ready.task_id


def _topological_layers(steps: tuple[PlanningStepDraft, ...]) -> tuple[tuple[str, ...], ...]:
    dependencies = {step.key: set(step.depends_on) for step in steps}
    completed: set[str] = set()
    layers: list[tuple[str, ...]] = []
    while len(completed) < len(dependencies):
        ready = tuple(
            sorted(
                key
                for key, required in dependencies.items()
                if key not in completed and required.issubset(completed)
            )
        )
        if not ready:
            return ()
        layers.append(ready)
        completed.update(ready)
    return tuple(layers)


def _step_contract(steps: tuple[PlanningStepDraft, ...]) -> list[JsonValue]:
    result: list[JsonValue] = []
    for step in steps:
        assignment = step.assignment
        result.append(
            {
                "key": step.key,
                "title": step.title,
                "objective": step.objective,
                "depends_on": list(step.depends_on),
                "agent_id": None if assignment is None else assignment.agent_id,
                "agent_revision": None if assignment is None else assignment.agent_revision,
                "capability_ids": [
                    requirement.capability_id
                    for requirement in step.capability_requirements
                    if requirement.required
                ],
                "requires_model": step.requires_model,
                "model_config_id": step.model_requirements.explicit_model_id,
            }
        )
    return result


__all__ = ["ReferencePlanningEvaluationCaseExecutor"]
