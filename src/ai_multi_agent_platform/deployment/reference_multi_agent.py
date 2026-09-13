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
    PlanningRequest,
    PlanningStepDraft,
)
from ai_multi_agent_platform.security import ActorIdentity, ActorType

from .handoff_composition import HandoffDeploymentComposition

_REFERENCE_ROLES = frozenset({"researcher", "developer", "reviewer"})


def _has_reference_roles(request: PlanningRequest) -> bool:
    """Detect the standard golden-path roles without becoming a selection authority."""

    enabled_roles = {candidate.role for candidate in request.inventory.agents if candidate.enabled}
    return _REFERENCE_ROLES.issubset(enabled_roles)


class ReferenceMultiAgentPlanner(DeterministicReferencePlanner):
    """Deterministic #439 planner for the built-in multi-agent golden path.

    The planner expresses role requirements only. Proposal construction then delegates actual
    eligibility and exact immutable Agent revision selection to the canonical #903 matcher already
    owned by #439. If the standard role set is unavailable, the ordinary single-Agent deterministic
    reference plan remains unchanged.
    """

    def __init__(self) -> None:
        super().__init__(planner_id="reference-multi-agent-planner")

    async def propose(self, request: PlanningRequest) -> PlannerOutput:
        if not _has_reference_roles(request):
            return await super().propose(request)

        reused = ()
        if request.prior_plan is not None:
            reused = request.prior_plan.completed_step_ids

        research_assignment = AgentAssignment(
            role_requirement="researcher",
            rationale=(
                "reference golden path: resolve Research Agent through canonical #903 matcher"
            ),
        )
        execution_assignment = AgentAssignment(
            role_requirement="developer",
            rationale=(
                "reference golden path: resolve Execution Agent through canonical #903 matcher"
            ),
        )
        review_assignment = AgentAssignment(
            role_requirement="reviewer",
            rationale="reference golden path: resolve Review Agent through canonical #903 matcher",
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
