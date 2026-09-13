from __future__ import annotations

from ai_multi_agent_platform.distributed import control_plane_ha as canonical
from ai_multi_agent_platform.high_availability import contracts as compatibility


def test_high_availability_contracts_reexport_canonical_distributed_objects() -> None:
    public_names = (
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
    )

    for name in public_names:
        assert getattr(compatibility, name) is getattr(canonical, name)
