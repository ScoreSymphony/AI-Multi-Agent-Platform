"""Canonical compensation and reversibility subsystem."""

from .control_plane import (
    CompensationActionView,
    CompensationControlPlaneProjection,
    CompensationGroupView,
)
from .coordinator import CompensationCoordinator
from .integration import PlanCompensationHooks
from .models import (
    CompensationActionProjection,
    CompensationAutomation,
    CompensationExecutionContext,
    CompensationFailureMode,
    CompensationGroup,
    CompensationGroupProjection,
    CompensationPolicy,
    CompensationReconciliation,
    CompensationRequest,
    CompensationResult,
    CompensationStatus,
    CompensationTrigger,
    CompletedSideEffect,
    new_compensation_action_id,
    new_compensation_group_id,
    new_compensation_id,
)
from .repository import (
    CompensationRepository,
    InMemoryCompensationRepository,
    SQLiteCompensationRepository,
)
from .service import (
    ApprovalReferenceLookup,
    CompensationReconciler,
    CompensationVerificationHook,
    ExecutionContextFactory,
)

__all__ = [
    "ApprovalReferenceLookup",
    "CompensationActionProjection",
    "CompensationActionView",
    "CompensationAutomation",
    "CompensationControlPlaneProjection",
    "CompensationCoordinator",
    "CompensationExecutionContext",
    "CompensationFailureMode",
    "CompensationGroup",
    "CompensationGroupProjection",
    "CompensationGroupView",
    "CompensationPolicy",
    "CompensationReconciler",
    "CompensationReconciliation",
    "CompensationRepository",
    "CompensationRequest",
    "CompensationResult",
    "CompensationStatus",
    "CompensationTrigger",
    "CompensationVerificationHook",
    "CompletedSideEffect",
    "ExecutionContextFactory",
    "InMemoryCompensationRepository",
    "PlanCompensationHooks",
    "SQLiteCompensationRepository",
    "new_compensation_action_id",
    "new_compensation_group_id",
    "new_compensation_id",
]
