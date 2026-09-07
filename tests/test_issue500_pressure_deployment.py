from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.contracts import AdapterMetadata
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.deployment.host_pressure import (
    HostPressureDeploymentConfig,
    configure_distributed_host_pressure,
)
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    HostPressureSnapshot,
    NodeRecord,
    PressureKind,
    PressureSignal,
    PressureState,
    RegistrationRequest,
    WorkerRecord,
    attach_pressure_report,
)
from ai_multi_agent_platform.distributed.pressure_control_plane import NodePressureResourceService
from ai_multi_agent_platform.distributed.pressure_reporting import authenticate_pressure_report
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.observability import InMemoryExporter, Telemetry

NOW = datetime(2026, 9, 7, 4, 0, tzinfo=UTC)


def _runtime_with_report(
    state: PressureState = PressureState.CRITICAL,
) -> tuple[DistributedRuntime, NodeRecord, WorkerRecord]:
    registry = DistributedRegistry()
    node = NodeRecord(node_id=new_id("node"), display_name="pressure-deployment-node")
    worker = WorkerRecord(worker_id=new_id("worker"), node_id=node.node_id)
    snapshot = HostPressureSnapshot(
        state=state,
        observed_at=NOW,
        signals=(PressureSignal(PressureKind.MEMORY, state, 42.0, "percent_stall_avg10"),),
        source_ref="linux:/private/source",
        provider_metadata=(
            AdapterMetadata("linux.host_pressure", {"private_path": "/sys/private"}),
        ),
    )
    reported = attach_pressure_report(worker, snapshot)
    authenticated = authenticate_pressure_report(
        reported,
        node_id=node.node_id,
        reporter_worker_id=worker.worker_id,
        accepted_at=NOW,
    )
    registry.register(RegistrationRequest(node=node, workers=(authenticated,)), now=NOW)
    return DistributedRuntime(registry), node, authenticated


def test_host_pressure_deployment_config_is_explicit_and_validated() -> None:
    disabled = HostPressureDeploymentConfig.from_environment({})
    assert disabled.enabled is False

    enabled = HostPressureDeploymentConfig.from_environment(
        {
            "PLATFORM_HOST_PRESSURE_ENABLED": "true",
            "PLATFORM_HOST_PRESSURE_REQUIRE_REPORT": "yes",
            "PLATFORM_HOST_PRESSURE_MAX_AGE_SECONDS": "12.5",
            "PLATFORM_HOST_PRESSURE_HEADROOM_CPU_CORES": "1.5",
            "PLATFORM_HOST_PRESSURE_HEADROOM_RAM_BYTES": "4096",
            "PLATFORM_HOST_PRESSURE_HEADROOM_STORAGE_BYTES": "8192",
        }
    )
    policy = enabled.policy()

    assert enabled.enabled is True
    assert policy.require_pressure_report is True
    assert policy.max_snapshot_age == timedelta(seconds=12.5)
    assert policy.protected_headroom.cpu_cores == 1.5
    assert policy.protected_headroom.ram_bytes == 4096
    assert policy.protected_headroom.storage_bytes == 8192

    with pytest.raises(ValueError, match="must be true/false"):
        HostPressureDeploymentConfig.from_environment(
            {"PLATFORM_HOST_PRESSURE_ENABLED": "sometimes"}
        )
    with pytest.raises(ValueError, match="greater than zero"):
        HostPressureDeploymentConfig(max_snapshot_age_seconds=0)


def test_disabled_deployment_hook_leaves_scheduler_pressure_unconfigured() -> None:
    runtime, _node, _worker = _runtime_with_report()

    provider = configure_distributed_host_pressure(
        runtime,
        Telemetry(InMemoryExporter()),
        HostPressureDeploymentConfig(enabled=False),
    )

    assert provider is None
    assert runtime.scheduler.pressure_provider is None
    assert runtime.scheduler.pressure_policy is None


def test_enabled_deployment_hook_uses_authenticated_registry_report_and_existing_telemetry() -> None:
    runtime, node, _worker = _runtime_with_report()
    exporter = InMemoryExporter()

    provider = configure_distributed_host_pressure(
        runtime,
        Telemetry(exporter),
        HostPressureDeploymentConfig(enabled=True, require_pressure_report=True),
    )

    assert provider is not None
    assert runtime.scheduler.pressure_provider is provider
    assert runtime.scheduler.pressure_policy is not None
    snapshot = provider.snapshot_for_node(node.node_id)
    assert snapshot is not None
    assert snapshot.trusted is True
    assert snapshot.state is PressureState.CRITICAL


def test_pressure_control_plane_projects_only_portable_safe_evidence() -> None:
    runtime, node, _worker = _runtime_with_report(PressureState.ELEVATED)
    provider = configure_distributed_host_pressure(
        runtime,
        Telemetry(InMemoryExporter()),
        HostPressureDeploymentConfig(enabled=True),
    )
    assert provider is not None
    service = NodePressureResourceService(runtime, provider)

    async def scenario() -> dict[str, object]:
        resource = await service.get_resource(
            RequestContext(request_id="pressure-doctor", correlation_id="pressure-doctor"),
            node.node_id,
        )
        listed = await service.list_resources(
            RequestContext(request_id="pressure-list", correlation_id="pressure-list"),
            PageQuery(),
        )
        assert listed == (resource,)
        return resource

    resource = asyncio.run(scenario())

    assert resource["id"] == node.node_id
    assert resource["state"] == "elevated"
    assert resource["trusted"] is True
    serialized = repr(resource)
    assert "linux:/private/source" not in serialized
    assert "/sys/private" not in serialized
    assert "linux.host_pressure" not in serialized


def test_pressure_control_plane_keeps_missing_report_explicitly_unknown() -> None:
    registry = DistributedRegistry()
    node = NodeRecord(node_id=new_id("node"), display_name="unknown-pressure-node")
    worker = WorkerRecord(worker_id=new_id("worker"), node_id=node.node_id)
    registry.register(RegistrationRequest(node=node, workers=(worker,)), now=NOW)
    runtime = DistributedRuntime(registry)
    provider = configure_distributed_host_pressure(
        runtime,
        Telemetry(InMemoryExporter()),
        HostPressureDeploymentConfig(enabled=True),
    )
    assert provider is not None
    service = NodePressureResourceService(runtime, provider)

    resource = asyncio.run(
        service.get_resource(
            RequestContext(request_id="pressure-unknown", correlation_id="pressure-unknown"),
            node.node_id,
        )
    )

    assert resource["state"] == "unknown"
    assert resource["observed_at"] is None
    assert resource["trusted"] is False
    assert resource["signals"] == []
