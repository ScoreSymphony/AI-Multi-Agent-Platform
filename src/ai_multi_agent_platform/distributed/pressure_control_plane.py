"""Control Plane visibility for portable host-pressure state from issue #500."""

from __future__ import annotations

from datetime import datetime

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .pressure import HostPressureSnapshot, PressureAdmissionPolicy, PressureState
from .registry import RegistryError
from .runtime import DistributedRuntime
from .models import utc_now

NODE_PRESSURE_COLLECTION = "node-pressure"


class NodePressureResourceService:
    """Read-only, sanitized host-pressure diagnostics for participating Nodes."""

    search_indexable = False

    def __init__(self, runtime: DistributedRuntime) -> None:
        self.runtime = runtime

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        now = utc_now()
        return tuple(
            _pressure_resource(self.runtime, node.node_id, now=now)
            for node in self.runtime.registry.list_nodes()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        try:
            self.runtime.registry.get_node(resource_id)
        except RegistryError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"node pressure diagnostics not found: {resource_id}",
            ) from exc
        return _pressure_resource(self.runtime, resource_id, now=utc_now())


def register_pressure_control_plane(
    control_plane: ControlPlane,
    runtime: DistributedRuntime,
) -> None:
    """Register #500 diagnostics on the existing authenticated Control Plane."""

    control_plane.register_resource_service(
        NODE_PRESSURE_COLLECTION,
        NodePressureResourceService(runtime),
    )


def _pressure_resource(
    runtime: DistributedRuntime,
    node_id: str,
    *,
    now: datetime,
) -> dict[str, JsonValue]:
    scheduler = runtime.scheduler
    policy = scheduler.pressure_policy
    provider = scheduler.pressure_provider
    if policy is None:
        return {
            "id": node_id,
            "enabled": False,
            "state": PressureState.UNKNOWN.value,
            "report_status": "disabled",
            "observed_at": None,
            "snapshot_age_seconds": None,
            "signals": [],
        }

    snapshot = None if provider is None else provider.snapshot_for_node(node_id)
    effective_state, report_status, age = _effective_pressure(snapshot, policy, now=now)
    return {
        "id": node_id,
        "enabled": True,
        "state": effective_state.value,
        "report_status": report_status,
        "observed_at": None if snapshot is None else snapshot.observed_at.isoformat(),
        "snapshot_age_seconds": age,
        "signals": []
        if snapshot is None
        else [_signal_resource(signal) for signal in snapshot.signals],
        "policy": {
            "max_snapshot_age_seconds": policy.max_snapshot_age.total_seconds(),
            "require_pressure_report": policy.require_pressure_report,
            "protected_headroom": {
                "cpu_cores": policy.protected_headroom.cpu_cores,
                "ram_bytes": policy.protected_headroom.ram_bytes,
                "storage_bytes": policy.protected_headroom.storage_bytes,
            },
        },
    }


def _effective_pressure(
    snapshot: HostPressureSnapshot | None,
    policy: PressureAdmissionPolicy,
    *,
    now: datetime,
) -> tuple[PressureState, str, float | None]:
    if snapshot is None:
        return PressureState.UNKNOWN, "missing", None
    age = max(0.0, (now - snapshot.observed_at).total_seconds())
    if not snapshot.trusted:
        return PressureState.UNKNOWN, "untrusted", age
    if age > policy.max_snapshot_age.total_seconds():
        return PressureState.UNKNOWN, "stale", age
    if snapshot.state is PressureState.UNKNOWN:
        return PressureState.UNKNOWN, "current", age
    return snapshot.state, "current", age


def _signal_resource(signal: object) -> dict[str, JsonValue]:
    # Kept separate so provider metadata/source references cannot accidentally enter this API.
    from .pressure import PressureSignal

    if not isinstance(signal, PressureSignal):  # pragma: no cover - tuple contract enforces this
        raise TypeError("pressure signal must be PressureSignal")
    return {
        "kind": signal.kind.value,
        "state": signal.state.value,
        "value": signal.value,
        "unit": signal.unit,
    }


__all__ = [
    "NODE_PRESSURE_COLLECTION",
    "NodePressureResourceService",
    "register_pressure_control_plane",
]
