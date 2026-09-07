"""Deterministic evidence-based Goal success evaluation."""

from __future__ import annotations

from collections.abc import Sequence

from .models import (
    CriterionEvaluation,
    GoalCriterionKind,
    GoalCriterionState,
    GoalEvidence,
    GoalTaskLink,
    GoalTaskState,
    SuccessCriterion,
)


def evaluate_criteria(
    criteria: Sequence[SuccessCriterion],
    evidence: Sequence[GoalEvidence],
    linked_tasks: Sequence[GoalTaskLink],
) -> tuple[CriterionEvaluation, ...]:
    return tuple(_evaluate(criterion, evidence, linked_tasks) for criterion in criteria)


def required_criteria_satisfied(
    criteria: Sequence[SuccessCriterion], evaluations: Sequence[CriterionEvaluation]
) -> bool:
    states = {item.criterion_id: item.state for item in evaluations}
    return all(
        not criterion.required or states.get(criterion.criterion_id) is GoalCriterionState.SATISFIED
        for criterion in criteria
    )


def _evaluate(
    criterion: SuccessCriterion,
    evidence: Sequence[GoalEvidence],
    linked_tasks: Sequence[GoalTaskLink],
) -> CriterionEvaluation:
    if criterion.kind is GoalCriterionKind.LINKED_TASKS:
        return _evaluate_linked_tasks(criterion, linked_tasks)

    matches = tuple(item for item in evidence if item.criterion_id == criterion.criterion_id)
    verified = tuple(item for item in matches if item.verified)
    if not verified:
        return CriterionEvaluation(
            criterion_id=criterion.criterion_id,
            state=(GoalCriterionState.UNSATISFIED if matches else GoalCriterionState.UNKNOWN),
            evidence_ids=tuple(item.evidence_id for item in matches),
            reason=(
                "matching evidence exists but is not verified"
                if matches
                else "no matching canonical evidence"
            ),
        )

    latest = max(verified, key=lambda item: item.observed_at)
    satisfied = False
    reason = "verified evidence does not satisfy the criterion"

    if criterion.kind is GoalCriterionKind.HUMAN_ACCEPTANCE:
        satisfied = latest.kind == "human_acceptance" and latest.value is True
        reason = (
            "verified human acceptance recorded"
            if satisfied
            else "latest verified evidence is not affirmative human acceptance"
        )
    elif criterion.kind is GoalCriterionKind.METRIC:
        satisfied = _compare_metric(latest.value, criterion.target, criterion.operator)
        reason = "verified metric comparison satisfied" if satisfied else reason
    elif criterion.kind is GoalCriterionKind.CANONICAL_STATE:
        satisfied = latest.kind == "canonical_state" and latest.value == criterion.target
        reason = "verified canonical state matched" if satisfied else reason
    elif criterion.kind is GoalCriterionKind.VERIFIED_ASSERTION:
        satisfied = latest.value is True
        reason = "verified assertion satisfied" if satisfied else reason

    return CriterionEvaluation(
        criterion_id=criterion.criterion_id,
        state=(GoalCriterionState.SATISFIED if satisfied else GoalCriterionState.UNSATISFIED),
        evidence_ids=(latest.evidence_id,),
        reason=reason,
    )


def _evaluate_linked_tasks(
    criterion: SuccessCriterion, linked_tasks: Sequence[GoalTaskLink]
) -> CriterionEvaluation:
    relevant = tuple(link for link in linked_tasks if link.valid_for_current_revision)
    successful = sum(link.task_state is GoalTaskState.SUCCEEDED for link in relevant)
    if isinstance(criterion.target, bool) or not isinstance(criterion.target, int):
        minimum = 1
    else:
        minimum = max(1, criterion.target)
    satisfied = successful >= minimum
    return CriterionEvaluation(
        criterion_id=criterion.criterion_id,
        state=(GoalCriterionState.SATISFIED if satisfied else GoalCriterionState.UNSATISFIED),
        reason=f"{successful} valid linked Task(s) succeeded; required {minimum}",
    )


def _compare_metric(value: object, target: object, operator: str) -> bool:
    if operator == "truthy":
        return bool(value)
    if operator == "eq":
        return value == target
    if isinstance(value, bool) or isinstance(target, bool):
        return False
    if not isinstance(value, int | float) or not isinstance(target, int | float):
        return False
    if operator == "gte":
        return value >= target
    if operator == "lte":
        return value <= target
    if operator == "gt":
        return value > target
    if operator == "lt":
        return value < target
    return False


__all__ = ["evaluate_criteria", "required_criteria_satisfied"]
