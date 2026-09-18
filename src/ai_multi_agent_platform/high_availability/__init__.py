"""Compatibility namespace for canonical :mod:`ai_multi_agent_platform.distributed.high_availability`."""

from .contracts import (
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
from .reconciliation import DistributedRuntimeFailoverReconciler
from .reference import InMemoryCoordinationProvider
from .service import ControlPlaneFailoverService
from .telemetry import HighAvailabilityTelemetry

__all__ = [
    "AuthorityGrant",
    "AvailabilityMode",
    "ControlPlaneFailoverService",
    "ControlPlaneHAStatus",
    "ControlPlaneRole",
    "CoordinationError",
    "CoordinationLease",
    "CoordinationProvider",
    "CoordinationState",
    "CoordinationUnavailable",
    "DistributedRuntimeFailoverReconciler",
    "FailoverReconciler",
    "FencingToken",
    "HighAvailabilityTelemetry",
    "InMemoryCoordinationProvider",
    "LeadershipConflict",
    "NotLeaderError",
    "PromotionReconciliationError",
    "ReconciliationResult",
    "StaleFencingToken",
]
