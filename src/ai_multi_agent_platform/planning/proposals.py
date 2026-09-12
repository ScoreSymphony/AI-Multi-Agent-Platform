"""Immutable planning proposal construction."""

from __future__ import annotations

from .agent_matching import resolve_planning_steps
from .models import (
    PlannerOutput,
    PlanningRequest,
    PlanProposal,
    new_plan_proposal_id,
)


class PlanningProposalFactory:
    """Construct provider-neutral immutable proposals from one planner response."""

    def build(self, request: PlanningRequest, output: PlannerOutput) -> PlanProposal:
        base = request.prior_plan
        return PlanProposal(
            proposal_id=new_plan_proposal_id(),
            task_id=request.task_id,
            task_revision=request.task_revision,
            plan_revision=1 if base is None else base.revision + 1,
            base_plan_id=None if base is None else base.plan_id,
            trigger=request.trigger,
            reason=request.reason,
            summary=output.draft.summary,
            steps=resolve_planning_steps(output.draft.steps, request),
            assumptions=output.draft.assumptions,
            constraints=tuple(
                dict.fromkeys((*request.task_constraints, *output.draft.constraints))
            ),
            evidence_refs=request.evidence_refs,
            planner=output.planner,
            model_config_id=output.model_config_id,
        )


__all__ = ["PlanningProposalFactory"]
