"""Read-only Control Plane projection for portable host-pressure diagnostics."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .pressure import HostPressureSnapshot, PressureSnapshotProvider
from .registry import RegistryError
from .runtime import DistributedRuntime

NODE_PRESSURE_COLLECTION = "node-pressure"


class NodePressureResourceService:
    """Project ephemeral portable pressure evidence without exposing provider-private metadata."""

    search_indexable = False

    def __init__(
        self,
        runtime: DistributedRuntime,
        provider: PressureSnapshotProvider,
    ) -> None:
        self.runtime = runtime
        self.provider = provider

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            _pressure_resource(node.node_id, self.provider.snapshot_for_node(node.node_id))
            for node in self.runtime.registry.list_nodes()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        try:
            node = self.runtime.registry.get_node(resource_id)
        except RegistryError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"node not found: {resource_id}") from exc
        return _pressure_resource(node.node_id, self.provider.snapshot_for_node(node.node_id))


def register_pressure_control_plane(
    control_plane: ControlPlane,
    runtime: DistributedRuntime,
    provider: PressureSnapshotProvider,
) -> None:
    """Register portable diagnostics beside, not inside, canonical Node capacity state."""

    control_plane.register_resource_service(
        NODE_PRESSURE_COLLECTION,
        NodePressureResourceService(runtime, provider),
    )


def _pressure_resource(
    node_id: str,
    snapshot: HostPressureSnapshot | None,
) -> dict[str, JsonValue]:
    if snapshot is None:
        return {
            "id": node_id,
            "node_id": node_id,
            "state": "unknown",
            "observed_at": None,
            "trusted": False,
            "signals": [],
        }
    return {
        "id": node_id,
        "node_id": node_id,
        "state": snapshot.state.value,
        "observed_at": snapshot.observed_at.isoformat(),
        "trusted": snapshot.trusted,
        "signals": [
            {
                "kind": signal.kind.value,
                "state": signal.state.value,
                "value": signal.value,
                "unit": signal.unit,
            }
            for signal in snapshot.signals
        ],
    }


__all__ = [
    "NODE_PRESSURE_COLLECTION",
    "NodePressureResourceService",
    "register_pressure_control_plane",
]
