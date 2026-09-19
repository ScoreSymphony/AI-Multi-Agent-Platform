"""Compatibility exports for canonical Control Plane HA contracts."""

from ai_multi_agent_platform.distributed.high_availability.contracts import (
    AuthorityGrant,
    AvailabilityMode,
    ControlPlaneHAStatus,
    ControlPlaneRole,
    CoordinationError,
    CoordinationLease,
    CoordinationProvider,
    CoordinationState,
    CoordinationUnavailable,
    FailoverReconciler,
    FencingToken,
    LeadershipConflict,
    NotLeaderError,
    PromotionReconciliationError,
    ReconciliationResult,
    StaleFencingToken,
)

__all__ = [
    "AuthorityGrant",
    "AvailabilityMode",
    "ControlPlaneHAStatus",
    "ControlPlaneRole",
    "CoordinationError",
    "CoordinationLease",
    "CoordinationProvider",
    "CoordinationState",
    "CoordinationUnavailable",
    "FailoverReconciler",
    "FencingToken",
    "LeadershipConflict",
    "NotLeaderError",
    "PromotionReconciliationError",
    "ReconciliationResult",
    "StaleFencingToken",
]
