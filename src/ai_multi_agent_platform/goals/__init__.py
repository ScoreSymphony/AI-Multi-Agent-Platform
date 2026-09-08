"""Durable Goal lifecycle above canonical executable Tasks."""

from .automation import (
    GOAL_OBSERVATION_PAYLOAD_KEY,
    GoalAutomationDispatchResult,
    GoalEvidenceResolver,
    dispatch_goal_automation_delivery,
)
from .criteria import evaluate_criteria, required_criteria_satisfied
from .models import (
    AutonomyPolicy,
    CriterionEvaluation,
    GoalConstraints,
    GoalCriterionKind,
    GoalCriterionState,
    GoalEvidence,
    GoalProgress,
    GoalReview,
    GoalState,
    GoalStatus,
    GoalTaskLink,
    GoalTaskState,
    ObservationPolicy,
    SuccessCriterion,
    TaskGenerationPolicy,
    TaskRevisionPolicy,
)
from .repository import EventSourcedGoalRepository, GoalRepository
from .service import GoalService, GoalTaskCreator, KernelGoalTaskCreator

__all__ = [
    "GOAL_OBSERVATION_PAYLOAD_KEY",
    "AutonomyPolicy",
    "CriterionEvaluation",
    "EventSourcedGoalRepository",
    "GoalAutomationDispatchResult",
    "GoalConstraints",
    "GoalCriterionKind",
    "GoalCriterionState",
    "GoalEvidence",
    "GoalEvidenceResolver",
    "GoalProgress",
    "GoalRepository",
    "GoalReview",
    "GoalService",
    "GoalState",
    "GoalStatus",
    "GoalTaskCreator",
    "GoalTaskLink",
    "GoalTaskState",
    "KernelGoalTaskCreator",
    "ObservationPolicy",
    "SuccessCriterion",
    "TaskGenerationPolicy",
    "TaskRevisionPolicy",
    "dispatch_goal_automation_delivery",
    "evaluate_criteria",
    "required_criteria_satisfied",
]
