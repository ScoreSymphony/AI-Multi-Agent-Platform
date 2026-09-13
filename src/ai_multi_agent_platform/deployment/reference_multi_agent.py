"""Reference multi-agent composition over existing platform runtime authorities (#889).

The code here deliberately owns no Task/Run, Plan/Step, Handoff, Context or verification state.
It supplies a deterministic reference planner behind #439 and a source adapter that binds incoming
#651 Handoffs to the exact consuming Run immediately before the existing #590 Context resolver
assembles that Run's immutable ContextBundle.
"""

from __future__ import annotations

from ai_multi_agent_platform.agents import AgentRevisionRef
from ai_multi_agent_platform.context import ContextCandidate, ContextSourceRequest
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.handoffs.production import DurableConsumedHandoffContextAdapter
from ai_multi_agent_platform.planning import DeterministicReferencePlanner
from ai_multi_agent_platform.planning.models import (
    AgentAssignment,
    PlanDraft,
    PlannerOutput,
    PlanningAgentCandidate,
    PlanningRequest,
    PlanningStepDraft,
)
from ai_multi_agent_platform.security import ActorIdentity, ActorType

from .handoff_composition import HandoffDeploymentComposition


def _normalized_role(role: str) -> str:
    return " ".join(role.strip().casefold().replace("_", " ").replace("-", " ").split())


def _select_role_candidate(
    candidates: tuple[PlanningAgentCandidate, ...],
    accepted_roles: frozenset[str],
) -> PlanningAgentCandidate | None:
    eligible = tuple(
        candidate
        for candidate in candidates
        if candidate.enabled and _normalized_role(candidate.role) in accepted_roles
    )
    if not eligible:
        return None
    return min(eligible, key=lambda item: (item.agent_id, item.revision))


class ReferenceMultiAgentPlanner(DeterministicReferencePlanner):
    """Deterministic #439 planner for the built-in multi-agent golden path.

    It activates only when the authorized planning inventory contains three distinct canonical
    Research, Execution and Review Agents. Otherwise the ordinary single-Agent deterministic
    reference plan remains unchanged.
    """

    _RESEARCH_ROLES = frozenset({"research", "researcher", "research agent"})
    _EXECUTION_ROLES = frozenset(
        {
            "coding",
            "coder",
            "developer",
            "execution",
            "executor",
            "coding agent",
            "execution agent",
        }
    )
    _REVIEW_ROLES = frozenset({"review", "reviewer", "review agent", "verification"})

    def __init__(self) -> None:
        super().__init__(planner_id="reference-multi-agent-planner")

    async def propose(self, request: PlanningRequest) -> PlannerOutput:
        candidates = request.inventory.agents
        research = _select_role_candidate(candidates, self._RESEARCH_ROLES)
        execution = _select_role_candidate(candidates, self._EXECUTION_ROLES)
        review = _select_role_candidate(candidates, self._REVIEW_ROLES)
        if research is None or execution is None or review is None:
            return await super().propose(request)
        if len({research.agent_id, execution.agent_id, review.agent_id}) != 3:
            return await super().propose(request)

        reused = ()
        if request.prior_plan is not None:
            reused = request.prior_plan.completed_step_ids

        research_assignment = AgentAssignment(
            agent_id=research.agent_id,
            agent_revision=research.revision,
            rationale="reference golden path: exact Research Agent revision",
        )
        execution_assignment = AgentAssignment(
            agent_id=execution.agent_id,
            agent_revision=execution.revision,
            rationale="reference golden path: exact Execution Agent revision",
        )
        review_assignment = AgentAssignment(
            agent_id=review.agent_id,
            agent_revision=review.revision,
            rationale="reference golden path: exact Review Agent revision",
        )
        draft = PlanDraft(
            summary="Reference multi-agent research, execution and review plan",
            steps=(
                PlanningStepDraft(
                    key="research",
                    title="Gather authoritative evidence",
                    objective=(
                        "Research the task objective and produce evidence that downstream work can "
                        "consume through canonical Handoff/Context state. " + request.objective
                    ),
                    assignment=research_assignment,
                    model_requirements=research.model_requirements,
                    requires_model=True,
                ),
                PlanningStepDraft(
                    key="approach",
                    title="Prepare an independent execution approach",
                    objective=(
                        "Independently analyze the requested outcome and prepare an execution "
                        "approach without waiting for the research branch. " + request.objective
                    ),
                    assignment=execution_assignment,
                    model_requirements=execution.model_requirements,
                    requires_model=True,
                ),
                PlanningStepDraft(
                    key="execute",
                    title="Produce the requested result",
                    objective=(
                        "Produce the task result using the completed research and "
                        "execution-approach Handoffs as canonical Context. " + request.objective
                    ),
                    depends_on=("research", "approach"),
                    assignment=execution_assignment,
                    model_requirements=execution.model_requirements,
                    requires_model=True,
                    reuse_step_ids=reused,
                ),
                PlanningStepDraft(
                    key="review",
                    title="Review the exact produced result",
                    objective=(
                        "Review the exact result revision produced by the execution step and "
                        "report whether it satisfies the task objective. " + request.objective
                    ),
                    depends_on=("execute",),
                    assignment=review_assignment,
                    model_requirements=review.model_requirements,
                    requires_model=True,
                ),
            ),
            constraints=request.task_constraints,
        )
        return PlannerOutput(draft=draft, planner=self.descriptor)


class ReferenceIncomingHandoffContextAdapter:
    """Bind all incoming Handoffs, then project them through the existing durable #590 adapter.

    Consumption remains owned by ``ProductionHandoffRuntime``. This adapter only performs that
    owner operation at the one safe boundary where #384 has already created the consumer Run and
    #590 has not yet assembled its ContextBundle. Replaying collection is idempotent because the
    canonical Handoff repository owns the durable consumption binding.
    """

    adapter_id = "reference-multi-agent-incoming-handoff/v1"

    def __init__(self, handoffs: HandoffDeploymentComposition) -> None:
        self._handoffs = handoffs
        self._durable = DurableConsumedHandoffContextAdapter(
            repository=handoffs.repository,
            agents=handoffs.runtime.agents,
        )

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        if request.step_id is None:
            return ()
        operation = getattr(request, "operation", None)
        if not isinstance(operation, OperationContext):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "reference Handoff Context adapter requires the operational Context request",
            )

        incoming = tuple(
            handoff
            for handoff in self._handoffs.service.list_handoffs_for_step(request.step_id)
            if handoff.content.consumer_step_id == request.step_id
            and handoff.content.task_id == request.task_id
            and (request.plan_id is None or handoff.content.plan_id == request.plan_id)
        )
        if not incoming:
            return ()

        consumer = AgentRevisionRef(request.agent_id, request.agent_revision)
        consumer_actor = ActorIdentity(request.agent_id, ActorType.AGENT)
        for handoff in sorted(incoming, key=lambda item: (item.handoff_id, item.revision)):
            await self._handoffs.runtime.consume_handoff(
                handoff.handoff_id,
                handoff.revision,
                consuming_run_id=request.run_id,
                consumer=consumer,
                consumer_actor=consumer_actor,
                operation=operation,
            )
        return await self._durable.collect(request)


__all__ = ["ReferenceIncomingHandoffContextAdapter", "ReferenceMultiAgentPlanner"]
