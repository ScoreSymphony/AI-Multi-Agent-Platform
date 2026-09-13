from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from ai_multi_agent_platform.cli.client import ClientResponse, ControlPlaneClient
from ai_multi_agent_platform.cli.compute import _doctor_host_pressure
from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.deployment.host_pressure import (
    HostPressureDeploymentConfig,
    configure_distributed_host_pressure,
)
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    DistributedTelemetry,
    HostPressureSnapshot,
    InMemoryPressureSnapshotProvider,
    NodeRecord,
    PressureKind,
    PressureSignal,
    PressureState,
    RegistrationRequest,
    ResourceSnapshot,
)
from ai_multi_agent_platform.distributed.pressure_control_plane import (
    NodePressureResourceService,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.observability import InMemoryExporter, Telemetry
from ai_multi_agent_platform.security import AuthorizationAction, ResourceType
from ai_multi_agent_platform.security.control_plane_bridge import (
    canonical_control_plane_vocabulary,
)

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _runtime_with_node() -> tuple[DistributedRuntime, NodeRecord]:
    runtime = DistributedRuntime(DistributedRegistry())
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="issue-500-node",
        resources=ResourceSnapshot(
            cpu_cores_total=8.0,
            cpu_cores_available=6.0,
            ram_total_bytes=32_000,
            ram_available_bytes=24_000,
            storage_total_bytes=100_000,
            storage_available_bytes=80_000,
        ),
        trust_level="trusted",
    )
    runtime.register(RegistrationRequest(node=node, workers=()), now=NOW)
    return runtime, node


def _snapshot(state: PressureState = PressureState.ELEVATED) -> HostPressureSnapshot:
    return HostPressureSnapshot(
        state=state,
        observed_at=NOW,
        trusted=True,
        signals=(
            PressureSignal(
                kind=PressureKind.MEMORY,
                state=state,
                value=42.0,
                unit="percent",
            ),
        ),
        source_ref="linux:/proc/pressure/memory",
        provider_metadata=(
            AdapterMetadata(
                namespace="linux.host_pressure",
                values={"path": "/proc/pressure/memory"},
            ),
        ),
    )


def test_host_pressure_config_is_opt_in_and_environment_driven() -> None:
    disabled = HostPressureDeploymentConfig.from_environment({})
    assert disabled.enabled is False

    enabled = HostPressureDeploymentConfig.from_environment(
        {
            "PLATFORM_HOST_PRESSURE_ENABLED": "true",
            "PLATFORM_HOST_PRESSURE_REQUIRE_REPORT": "true",
            "PLATFORM_HOST_PRESSURE_MAX_AGE_SECONDS": "45",
            "PLATFORM_HOST_PRESSURE_HEADROOM_CPU_CORES": "1.5",
            "PLATFORM_HOST_PRESSURE_HEADROOM_RAM_BYTES": "1024",
            "PLATFORM_HOST_PRESSURE_HEADROOM_STORAGE_BYTES": "2048",
        }
    )

    policy = enabled.policy()
    assert enabled.enabled is True
    assert enabled.require_pressure_report is True
    assert policy.max_snapshot_age == timedelta(seconds=45)
    assert policy.protected_headroom.cpu_cores == 1.5
    assert policy.protected_headroom.ram_bytes == 1024
    assert policy.protected_headroom.storage_bytes == 2048


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("PLATFORM_HOST_PRESSURE_MAX_AGE_SECONDS", "nan"),
        ("PLATFORM_HOST_PRESSURE_MAX_AGE_SECONDS", "inf"),
        ("PLATFORM_HOST_PRESSURE_HEADROOM_CPU_CORES", "-inf"),
    ),
)
def test_host_pressure_config_rejects_non_finite_environment_values(
    name: str,
    value: str,
) -> None:
    with pytest.raises(ValueError, match="finite"):
        HostPressureDeploymentConfig.from_environment({name: value})

    with pytest.raises(ValueError, match="finite"):
        HostPressureDeploymentConfig(protected_cpu_cores=float("nan"))


def test_distributed_pressure_composition_is_optional() -> None:
    runtime = DistributedRuntime(DistributedRegistry())
    telemetry = Telemetry(InMemoryExporter())
    provider = InMemoryPressureSnapshotProvider()

    assert (
        configure_distributed_host_pressure(
            runtime,
            telemetry,
            HostPressureDeploymentConfig(enabled=False),
            provider=provider,
        )
        is None
    )
    assert runtime.scheduler.pressure_provider is None

    resolved = configure_distributed_host_pressure(
        runtime,
        telemetry,
        HostPressureDeploymentConfig(enabled=True),
        provider=provider,
    )
    assert resolved is provider
    assert isinstance(runtime.telemetry, DistributedTelemetry)
    assert runtime.scheduler.pressure_provider is provider
    assert runtime.scheduler.pressure_policy is not None
    assert runtime.scheduler.pressure_telemetry is not None


def test_pressure_control_plane_projects_only_portable_evidence() -> None:
    runtime, node = _runtime_with_node()
    provider = InMemoryPressureSnapshotProvider()
    provider.put(node.node_id, _snapshot(PressureState.CRITICAL))
    service = NodePressureResourceService(runtime, provider)

    context = RequestContext(
        request_id="request-500-pressure",
        correlation_id="correlation-500-pressure",
    )
    resource = asyncio.run(service.get_resource(context, node.node_id))

    assert resource["id"] == node.node_id
    assert resource["state"] == "critical"
    assert resource["trusted"] is True
    assert resource["signals"] == [
        {
            "kind": "memory",
            "state": "critical",
            "value": 42.0,
            "unit": "percent",
        }
    ]
    assert "source_ref" not in resource
    assert "provider_metadata" not in resource


def test_pressure_control_plane_uses_node_authorization_vocabulary() -> None:
    assert canonical_control_plane_vocabulary("node-pressure:list") == (
        AuthorizationAction.VIEW,
        ResourceType.NODE,
    )
    assert canonical_control_plane_vocabulary("node-pressure:read") == (
        AuthorizationAction.READ,
        ResourceType.NODE,
    )


def test_doctor_pressure_is_optional_and_marks_critical_pressure_degraded() -> None:
    optional = _StubPressureClient(_response(404, None))
    status, checks = _doctor_host_pressure(cast(ControlPlaneClient, optional))
    assert status == "healthy"
    assert checks == []

    critical = _StubPressureClient(
        _response(
            200,
            {
                "items": [
                    {
                        "id": "node-a",
                        "node_id": "node-a",
                        "state": "critical",
                        "observed_at": NOW.isoformat(),
                        "trusted": True,
                        "signals": [],
                    }
                ]
            },
        )
    )
    status, checks = _doctor_host_pressure(cast(ControlPlaneClient, critical))
    assert status == "degraded"
    assert checks == [
        {
            "name": "host_pressure",
            "status": "degraded",
            "resource_id": "node-a",
            "pressure_state": "critical",
            "trusted": True,
            "observed_at": NOW.isoformat(),
        }
    ]


def test_doctor_pressure_follows_pagination_before_deciding_health() -> None:
    first = _response(
        200,
        {
            "items": [
                {
                    "id": "node-a",
                    "node_id": "node-a",
                    "state": "healthy",
                    "observed_at": NOW.isoformat(),
                    "trusted": True,
                    "signals": [],
                }
            ],
            "next_cursor": "cursor-2",
        },
    )
    second = _response(
        200,
        {
            "items": [
                {
                    "id": "node-b",
                    "node_id": "node-b",
                    "state": "critical",
                    "observed_at": NOW.isoformat(),
                    "trusted": True,
                    "signals": [],
                }
            ],
            "next_cursor": None,
        },
    )
    paged = _PagedPressureClient([first, second])

    status, checks = _doctor_host_pressure(cast(ControlPlaneClient, paged))

    assert status == "degraded"
    assert paged.queries == [
        {"limit": "200"},
        {"limit": "200", "cursor": "cursor-2"},
    ]
    assert any(
        item.get("resource_id") == "node-b" and item.get("status") == "degraded"
        for item in checks
        if isinstance(item, dict)
    )


def _response(status: int, body: JsonValue) -> ClientResponse:
    return ClientResponse(
        status=status,
        body=body,
        request_id="request-500",
        correlation_id="correlation-500",
        api_version="v1",
    )


class _StubPressureClient:
    def __init__(self, response: ClientResponse) -> None:
        self.response = response

    def get(
        self,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        raise_for_status: bool = True,
    ) -> ClientResponse:
        del query, raise_for_status
        assert path == "/node-pressure"
        return self.response


class _PagedPressureClient:
    def __init__(self, responses: list[ClientResponse]) -> None:
        self.responses = list(responses)
        self.queries: list[dict[str, str]] = []

    def get(
        self,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
        raise_for_status: bool = True,
    ) -> ClientResponse:
        del raise_for_status
        assert path == "/node-pressure"
        self.queries.append(dict(query or {}))
        if not self.responses:
            raise AssertionError("unexpected pressure page request")
        return self.responses.pop(0)
