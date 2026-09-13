"""Reference multi-agent composition over the existing platform runtime authorities (#889).

This module intentionally owns no Task/Run, Plan/Step, Handoff, Context or verification state.
It supplies a deterministic reference planner implementation behind #439 and a narrow bridge that
lets an exact Step-bound Agent consume already-persisted #651 Handoffs through #590 ContextBundles
before the existing Agent lifecycle performs its ordinary model/capability turn.
"""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.agents import AgentRevisionRef
from ai_multi_agent_platform.context import ContextBudget, rendered_context_model_input
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
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
from ai_multi_agent_platform.onboarding.agent_lifecycle import (
    ContextualStepAgentStart,
    StepContextAgentStarter,
)

from .handoff_composition import HandoffDeploymentComposition

_REFERENCE_CONTEXT_TOKEN_BUDGET = 16_384


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

    The planner activates only when the authorized planning inventory exposes distinct canonical
    Research, Execution and Review roles. Otherwise it deliberately falls back to the existing
    single-Agent deterministic reference plan, preserving ordinary deployments and tests.
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
                        "consume through canonical handoff/context state. " + request.objective
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
                        "Produce the task result using the completed research and execution-approach "
                        "handoffs as canonical context. " + request.objective
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
                        "Review the exact result revision produced by the execution step and report "
                        "whether it satisfies the task objective. " + request.objective
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


class ReferenceHandoffStepContextStarter(StepContextAgentStarter):
    """Consume incoming canonical Handoffs and start the exact Step Agent through #590."""

    def __init__(
        self,
        handoffs: HandoffDeploymentComposition,
        *,
        max_context_tokens: int = _REFERENCE_CONTEXT_TOKEN_BUDGET,
    ) -> None:
        if max_context_tokens < 1:
            raise ValueError("max_context_tokens must be >= 1")
        self._handoffs = handoffs
        self._budget = ContextBudget(max_tokens=max_context_tokens)

    async def start_contextual_agent(
        self,
        *,
        request,
        binding,
        task_id: str,
        project_id: str | None,
        task_model_override,
        requested_capability_ids: tuple[str, ...],
        available_capability_ids: frozenset[str],
        verification_context,
    ) -> ContextualStepAgentStart | None:
        del task_model_override
        incoming = tuple(
            handoff
            for handoff in self._handoffs.service.list_handoffs_for_step(request.subject_id)
            if handoff.content.consumer_step_id == request.subject_id
            and handoff.content.task_id == task_id
        )
        if not incoming:
            return None
        if binding.agent_revision is None:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "reference multi-agent Context execution requires an exact Agent revision",
            )

        consumer = AgentRevisionRef(binding.agent_id, binding.agent_revision)
        consumer_actor = ActorIdentity(binding.agent_id, ActorType.AGENT)
        operation = replace(request.context, project_id=project_id)
        ordered = tuple(sorted(incoming, key=lambda item: (item.handoff_id, item.revision)))

        # Bind every predecessor Handoff to this exact consuming Run before assembling Context.
        # The final call uses the existing production Handoff runtime to assemble one Bundle and
        # start exactly one AgentRun. The durable adapter then includes all bound fan-in Handoffs.
        for handoff in ordered[:-1]:
            await self._handoffs.runtime.consume_handoff(
                handoff.handoff_id,
                handoff.revision,
                consuming_run_id=request.run_id,
                consumer=consumer,
                consumer_actor=consumer_actor,
                operation=operation,
            )
        final = ordered[-1]
        execution = await self._handoffs.runtime.start_consumer(
            final.handoff_id,
            final.revision,
            consuming_run_id=request.run_id,
            consumer=consumer,
            consumer_actor=consumer_actor,
            operation=operation,
            budget=self._budget,
            consumer_agent=consumer,
            workspace_id=binding.workspace_id,
            requested_capability_ids=requested_capability_ids,
            available_capability_ids=available_capability_ids,
        )

        rendered = await self._handoffs.context_runtime.renderer.render(execution.context_bundle)
        model_input = rendered_context_model_input(rendered)
        revision = self._handoffs.context_runtime.runtime.service.get_agent_revision(
            binding.agent_id,
            binding.agent_revision,
        )
        role_instruction = revision.profile.instructions.role.content
        if role_instruction is None:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "reference multi-agent Context execution requires inline Agent role instructions",
            )
        instruction = "\n\n".join(
            part for part in (role_instruction, model_input.system_instruction) if part.strip()
        )
        objective = "\n\n".join(
            part
            for part in (
                binding.objective or "Continue the assigned canonical Step.",
                model_input.user_message,
            )
            if part.strip()
        )
        return ContextualStepAgentStart(
            agent_run=execution.agent_run,
            instruction=instruction,
            objective=objective,
        )


__all__ = ["ReferenceHandoffStepContextStarter", "ReferenceMultiAgentPlanner"]
