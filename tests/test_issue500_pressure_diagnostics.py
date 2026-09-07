from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest

from ai_multi_agent_platform.adapters.distributed_control_plane_app import _host_pressure_enabled
from ai_multi_agent_platform.cli.client import RawResponse
from ai_multi_agent_platform.cli.main import run_cli
from ai_multi_agent_platform.contracts import AdapterMetadata
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.distributed import (
    DeterministicScheduler,
    DistributedRegistry,
    DistributedRuntime,
    HostPressureSnapshot,
    InMemoryPressureSnapshotProvider,
    NodeRecord,
    PressureAdmissionPolicy,
    PressureKind,
    PressureSignal,
    PressureState,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerRecord,
    register_distributed_control_plane,
)
from ai_multi_agent_platform.distributed.pressure_control_plane import (
    register_pressure_control_plane,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class InProcessTransport:
    def __init__(self, http: ControlPlaneHTTP) -> None:
        self.http = http

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        del timeout
        parsed = urlsplit(url)
        decoded: dict[str, Any] = {}
        if body:
            loaded = json.loads(body.decode("utf-8"))
            assert isinstance(loaded, dict)
            decoded = loaded
        response = asyncio.run(
            self.http.handle(
                HTTPRequest(
                    method=method,
                    path=parsed.path,
                    headers=headers,
                    query=dict(parse_qsl(parsed.query)),
                    body=decoded,
                )
            )
        )
        return RawResponse(
            status=response.status,
            body=json.dumps(response.body, default=str).encode("utf-8"),
            headers=response.headers,
        )


def _stack(
    *,
    pressure_enabled: bool = True,
    max_age: timedelta = timedelta(seconds=30),
) -> tuple[InProcessTransport, DistributedRuntime, InMemoryPressureSnapshotProvider, NodeRecord]:
    repository = InMemoryKernelRepository()
    control_plane = ControlPlane(
        kernel=PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        ),
        events=repository,
    )
    registry = DistributedRegistry()
    provider = InMemoryPressureSnapshotProvider()
    scheduler = DeterministicScheduler(
        registry,
        pressure_provider=provider if pressure_enabled else None,
        pressure_policy=PressureAdmissionPolicy(max_snapshot_age=max_age)
        if pressure_enabled
        else None,
    )
    runtime = DistributedRuntime(registry, scheduler=scheduler)
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="issue-500-pressure-diagnostics",
        resources=ResourceSnapshot(
            cpu_cores_total=8,
            cpu_cores_available=8,
            ram_total_bytes=16_000,
            ram_available_bytes=16_000,
            storage_total_bytes=100_000,
            storage_available_bytes=100_000,
        ),
    )
    worker = WorkerRecord(worker_id=new_id("worker"), node_id=node.node_id)
    runtime.register(RegistrationRequest(node=node, workers=(worker,)))
    register_distributed_control_plane(control_plane, runtime)
    register_pressure_control_plane(control_plane, runtime)
    return InProcessTransport(ControlPlaneHTTP(control_plane)), runtime, provider, node


def _invoke_doctor(config: Path, transport: InProcessTransport) -> tuple[int, dict[str, Any], str]:
    stdout = StringIO()
    stderr = StringIO()
    code = run_cli(
        ["--config", str(config), "--json", "doctor"],
        transport=transport,
        stdout=stdout,
        stderr=stderr,
    )
    payload = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    assert isinstance(payload, dict)
    return code, payload, stderr.getvalue()


def _snapshot(
    state: PressureState,
    *,
    observed_at: datetime | None = None,
) -> HostPressureSnapshot:
    return HostPressureSnapshot(
        state=state,
        observed_at=observed_at or datetime.now(UTC),
        signals=(
            PressureSignal(
                kind=PressureKind.MEMORY,
                state=state,
                value=7.5,
                unit="percent_stall_avg10",
            ),
        ),
        source_ref="linux:/proc/pressure/memory",
        provider_metadata=(
            AdapterMetadata(
                "linux.host_pressure.private",
                {"path": "/sys/fs/cgroup/private", "secretish": "must-not-leak"},
            ),
        ),
    )


def _pressure_check(payload: dict[str, Any]) -> dict[str, Any]:
    checks = payload["data"]["checks"]
    matches = [item for item in checks if item.get("name") == "host_pressure"]
    assert len(matches) == 1
    return matches[0]


def test_pressure_control_plane_exposes_only_portable_sanitized_evidence() -> None:
    transport, _, provider, node = _stack()
    provider.put(node.node_id, _snapshot(PressureState.ELEVATED))

    raw = transport.request(
        "GET",
        f"http://platform.local/node-pressure/{node.node_id}",
        headers={"accept": "application/json"},
        body=None,
        timeout=1.0,
    )

    assert raw.status == 200
    payload = json.loads(raw.body.decode("utf-8"))
    resource = payload["data"]
    assert resource["id"] == node.node_id
    assert resource["enabled"] is True
    assert resource["state"] == "elevated"
    assert resource["report_status"] == "current"
    assert resource["signals"][0]["kind"] == "memory"
    serialized = json.dumps(payload)
    assert "/proc/pressure/memory" not in serialized
    assert "/sys/fs/cgroup/private" not in serialized
    assert "must-not-leak" not in serialized
    assert "provider_metadata" not in serialized
    assert "source_ref" not in serialized


def test_doctor_reports_fresh_healthy_pressure_as_healthy(tmp_path: Path) -> None:
    transport, _, provider, node = _stack()
    provider.put(node.node_id, _snapshot(PressureState.HEALTHY))

    code, payload, error = _invoke_doctor(tmp_path / "cli.json", transport)

    assert code == 0 and not error
    assert payload["data"]["summary"] == "healthy"
    check = _pressure_check(payload)
    assert check["status"] == "healthy"
    assert check["pressure_state"] == "healthy"
    assert check["report_status"] == "current"
    assert check["enabled"] is True


def test_doctor_reports_critical_pressure_as_degraded(tmp_path: Path) -> None:
    transport, _, provider, node = _stack()
    provider.put(node.node_id, _snapshot(PressureState.CRITICAL))

    code, payload, error = _invoke_doctor(tmp_path / "cli.json", transport)

    assert code == 1 and not error
    assert payload["data"]["summary"] == "degraded"
    check = _pressure_check(payload)
    assert check["status"] == "degraded"
    assert check["pressure_state"] == "critical"
    assert check["report_status"] == "current"


def test_doctor_never_treats_stale_or_missing_pressure_as_healthy(tmp_path: Path) -> None:
    transport, _, provider, node = _stack(max_age=timedelta(seconds=5))
    provider.put(
        node.node_id,
        _snapshot(
            PressureState.HEALTHY,
            observed_at=datetime.now(UTC) - timedelta(minutes=1),
        ),
    )

    code, stale, _ = _invoke_doctor(tmp_path / "cli.json", transport)
    assert code == 1
    stale_check = _pressure_check(stale)
    assert stale_check["pressure_state"] == "unknown"
    assert stale_check["report_status"] == "stale"

    provider.remove(node.node_id)
    code, missing, _ = _invoke_doctor(tmp_path / "cli.json", transport)
    assert code == 1
    missing_check = _pressure_check(missing)
    assert missing_check["pressure_state"] == "unknown"
    assert missing_check["report_status"] == "missing"


def test_doctor_keeps_disabled_pressure_visible_without_degrading_existing_deployments(
    tmp_path: Path,
) -> None:
    transport, _, _, _ = _stack(pressure_enabled=False)

    code, payload, error = _invoke_doctor(tmp_path / "cli.json", transport)

    assert code == 0 and not error
    check = _pressure_check(payload)
    assert check["status"] == "healthy"
    assert check["enabled"] is False
    assert check["pressure_state"] == "unknown"
    assert check["report_status"] == "disabled"


def test_distributed_pressure_opt_in_parser_is_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLATFORM_HOST_PRESSURE_ENABLED", raising=False)
    assert _host_pressure_enabled() is False

    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("PLATFORM_HOST_PRESSURE_ENABLED", value)
        assert _host_pressure_enabled() is True

    for value in ("0", "false", "No", "off", ""):
        monkeypatch.setenv("PLATFORM_HOST_PRESSURE_ENABLED", value)
        assert _host_pressure_enabled() is False

    monkeypatch.setenv("PLATFORM_HOST_PRESSURE_ENABLED", "sometimes")
    with pytest.raises(ValueError, match="PLATFORM_HOST_PRESSURE_ENABLED"):
        _host_pressure_enabled()
