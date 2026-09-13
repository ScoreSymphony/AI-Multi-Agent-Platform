from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "acceptance" / "two_vps_private_tunnel.py"
)
_COMMIT = "0123456789abcdef0123456789abcdef01234567"
_NODE_ID = "node_00000000-0000-4000-8000-000000000562"
_WORKER_ID = "worker_00000000-0000-4000-8000-000000000562"


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _phase(phase: str, **values: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "ai-multi-agent-platform/issue-562-platform-phase/v1",
        "phase": phase,
        "status": "pass",
        "observed_at": "2026-09-13T10:00:00+00:00",
        "platform_commit": _COMMIT,
        "control_host_label": "host-a",
        "worker_host_label": "host-b",
        "credential_material_recorded": False,
        "network_address_recorded": False,
    }
    payload.update(values)
    return payload


def _registration() -> dict[str, object]:
    return _phase(
        "registration",
        node_id=_NODE_ID,
        worker_id=_WORKER_ID,
        authentication_mode="worker-credential+https+mtls",
        authenticated=True,
        heartbeat_healthy=True,
        capability_refs=["execution:general"],
        cpu_cores=8,
        ram_bytes=17179869184,
    )


def _dispatch() -> dict[str, object]:
    return _phase(
        "dispatch",
        worker_id=_WORKER_ID,
        task_id="task_00000000-0000-4000-8000-000000000562",
        run_id="run_00000000-0000-4000-8000-000000000562",
        worker_job_id="worker_job_00000000-0000-4000-8000-000000000562",
        result_status="succeeded",
        correlation_id="corr-562",
        returned_correlation_id="corr-562",
        trace_id="trace-562",
        returned_trace_id="trace-562",
        correlation_preserved=True,
        trace_preserved=True,
        duplicate_run_count=0,
        selected_by_capability_policy=True,
    )


def _interruption() -> dict[str, object]:
    return _phase(
        "interruption",
        worker_id=_WORKER_ID,
        heartbeat_expired=True,
        worker_unavailable=True,
        new_placement_blocked=True,
        active_state_reconciled=True,
        indefinite_running_state=False,
    )


def _recovery() -> dict[str, object]:
    return _phase(
        "recovery",
        worker_id=_WORKER_ID,
        re_registered=True,
        heartbeat_recovered=True,
        stale_authority_rejected=True,
        duplicate_run_count=0,
        post_recovery_run_id="run_00000000-0000-4000-8000-000000000563",
        post_recovery_worker_job_id="worker_job_00000000-0000-4000-8000-000000000563",
        post_recovery_status="succeeded",
    )


def _restart() -> dict[str, object]:
    return _phase(
        "restart",
        worker_id=_WORKER_ID,
        first_process_evidence_ref="evidence:worker-process:first",
        second_process_evidence_ref="evidence:worker-process:second",
        worker_process_restarted=True,
        re_registered=True,
        heartbeat_recovered=True,
        post_restart_run_id="run_00000000-0000-4000-8000-000000000564",
        post_restart_status="succeeded",
    )


def _security() -> dict[str, object]:
    return _phase(
        "security",
        worker_ports_publicly_closed=True,
        unauthenticated_registration_rejected=True,
        invalid_credential_rejected=True,
        authorization_server_side=True,
        reusable_secrets_found=False,
        provider_metadata_canonicalized=False,
    )


def _probe(endpoint: str, scope: str) -> dict[str, object]:
    reachable = scope == "private"
    return {
        "schema": "ai-multi-agent-platform/issue-562-network-probe/v1",
        "status": "pass",
        "observed_at": "2026-09-13T10:00:00+00:00",
        "platform_commit": _COMMIT,
        "source_host_label": "host-b",
        "endpoint_label": endpoint,
        "scope": scope,
        "port": 8765 if endpoint == "message-broker" else 8443,
        "expected": "reachable" if reachable else "closed",
        "reachable": reachable,
        "outcome": "connected" if reachable else "refused",
        "latency_ms": 8.5 if reachable else 3.0,
        "error_class": None if reachable else "ConnectionRefusedError",
        "error_errno": None,
        "target_address_recorded": False,
        "credential_material_recorded": False,
    }


def _transport_report() -> dict[str, object]:
    return {
        "schema": "ai-multi-agent-platform/issue-388-two-host-transport/v1",
        "status": "pass",
        "worker_id": _WORKER_ID,
        "transport": {
            "provider": "TcpMessageTransport",
            "tls": True,
            "authentication": "mtls",
        },
        "artifact_refs": ["artifact_input", "artifact_output"],
        "evidence_refs": ["evidence:issue388-two-host"],
        "broker_address_recorded": False,
        "credential_material_recorded": False,
    }


def _materialize_evidence(tmp_path: Path) -> dict[str, Path]:
    payloads = {
        "registration": _registration(),
        "dispatch": _dispatch(),
        "interruption": _interruption(),
        "recovery": _recovery(),
        "restart": _restart(),
        "security": _security(),
    }
    paths: dict[str, Path] = {}
    for name, payload in payloads.items():
        path = tmp_path / f"{name}.json"
        _write(path, payload)
        paths[name] = path
    for endpoint in ("worker-protocol", "message-broker"):
        for scope in ("private", "public"):
            name = f"probe-{endpoint}-{scope}"
            path = tmp_path / f"{name}.json"
            _write(path, _probe(endpoint, scope))
            paths[name] = path
    transport = tmp_path / "transport.json"
    _write(transport, _transport_report())
    paths["transport"] = transport
    return paths


def _finalize_command(paths: dict[str, Path], report: Path) -> list[str]:
    command = [
        sys.executable,
        str(_SCRIPT),
        "finalize",
        "--registration",
        str(paths["registration"]),
        "--dispatch",
        str(paths["dispatch"]),
        "--interruption",
        str(paths["interruption"]),
        "--recovery",
        str(paths["recovery"]),
        "--restart",
        str(paths["restart"]),
        "--security",
        str(paths["security"]),
    ]
    for endpoint in ("worker-protocol", "message-broker"):
        for scope in ("private", "public"):
            command.extend(["--network-report", str(paths[f"probe-{endpoint}-{scope}"])])
    command.extend(
        [
            "--transport-report",
            str(paths["transport"]),
            "--json-report",
            str(report),
        ]
    )
    return command


def _run_finalizer(paths: dict[str, Path], report_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _finalize_command(paths, report_path),
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _load_acceptance_module():
    spec = importlib.util.spec_from_file_location("issue562_acceptance", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_finalizer_accepts_complete_sanitized_real_host_evidence(tmp_path: Path) -> None:
    paths = _materialize_evidence(tmp_path)
    report_path = tmp_path / "issue562.json"

    completed = _run_finalizer(paths, report_path)

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert report["platform_commit"] == _COMMIT
    assert report["canonical"]["worker_id"] == _WORKER_ID
    assert report["advertised_capability_refs"] == ["execution:general"]
    assert report["phases"] == {
        "dispatch": "pass",
        "interruption": "pass",
        "recovery": "pass",
        "registration": "pass",
        "restart": "pass",
        "security": "pass",
    }
    assert report["network"]["addresses_recorded"] is False
    assert report["conformance"]["scenario_id"] == "E"
    assert report["conformance"]["optional_real_infrastructure"] is True
    assert report["credential_material_recorded"] is False
    assert report["provider_or_tunnel_identity_canonicalized"] is False


def test_probe_rejects_address_resolution_failure(tmp_path: Path, monkeypatch) -> None:
    module = _load_acceptance_module()
    report_path = tmp_path / "probe.json"

    def fail_resolution(*_args, **_kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "unresolvable test target")

    monkeypatch.setattr(module.socket, "create_connection", fail_resolution)
    args = SimpleNamespace(
        port=8443,
        timeout_seconds=1.0,
        target_address="unresolvable.invalid",
        expect="closed",
        platform_commit=_COMMIT,
        source_host_label="host-b",
        endpoint_label="worker-protocol",
        scope="public",
        json_report=report_path,
    )

    with pytest.raises(module.AcceptanceError, match="did not resolve"):
        module._run_probe(args)

    assert not report_path.exists()


def test_finalizer_rejects_publicly_reachable_internal_service(tmp_path: Path) -> None:
    paths = _materialize_evidence(tmp_path)
    public_broker = paths["probe-message-broker-public"]
    payload = _probe("message-broker", "public")
    payload["reachable"] = True
    payload["outcome"] = "connected"
    payload["status"] = "fail"
    _write(public_broker, payload)
    report_path = tmp_path / "issue562.json"

    completed = _run_finalizer(paths, report_path)

    assert completed.returncode == 2
    assert "network evidence contains a non-passing/unsupported probe" in completed.stderr
    assert not report_path.exists()


def test_finalizer_rejects_mismatched_public_probe_port(tmp_path: Path) -> None:
    paths = _materialize_evidence(tmp_path)
    public_broker = paths["probe-message-broker-public"]
    payload = _probe("message-broker", "public")
    payload["port"] = 8766
    _write(public_broker, payload)
    report_path = tmp_path / "issue562.json"

    completed = _run_finalizer(paths, report_path)

    assert completed.returncode == 2
    assert "public/private probes must test the same service port" in completed.stderr
    assert not report_path.exists()


def test_finalizer_rejects_worker_identity_drift(tmp_path: Path) -> None:
    paths = _materialize_evidence(tmp_path)
    recovery = _recovery()
    recovery["worker_id"] = "worker_00000000-0000-4000-8000-000000009999"
    _write(paths["recovery"], recovery)
    report_path = tmp_path / "issue562.json"

    completed = _run_finalizer(paths, report_path)

    assert completed.returncode == 2
    assert "changed the canonical Worker identity" in completed.stderr
    assert not report_path.exists()


def test_finalizer_rejects_missing_advertised_capabilities(tmp_path: Path) -> None:
    paths = _materialize_evidence(tmp_path)
    registration = _registration()
    registration["capability_refs"] = []
    _write(paths["registration"], registration)
    report_path = tmp_path / "issue562.json"

    completed = _run_finalizer(paths, report_path)

    assert completed.returncode == 2
    assert "must contain advertised capabilities" in completed.stderr
    assert not report_path.exists()


def test_finalizer_rejects_reused_run_id_after_recovery(tmp_path: Path) -> None:
    paths = _materialize_evidence(tmp_path)
    recovery = _recovery()
    recovery["post_recovery_run_id"] = _dispatch()["run_id"]
    _write(paths["recovery"], recovery)
    report_path = tmp_path / "issue562.json"

    completed = _run_finalizer(paths, report_path)

    assert completed.returncode == 2
    assert "must use distinct canonical Run IDs" in completed.stderr
    assert not report_path.exists()


def test_finalizer_rejects_sensitive_manual_evidence_key(tmp_path: Path) -> None:
    paths = _materialize_evidence(tmp_path)
    registration = _registration()
    registration["token_value"] = "must-not-be-retained"
    _write(paths["registration"], registration)
    report_path = tmp_path / "issue562.json"

    completed = _run_finalizer(paths, report_path)

    assert completed.returncode == 2
    assert "unsafe evidence key is not allowed" in completed.stderr
    assert not report_path.exists()
