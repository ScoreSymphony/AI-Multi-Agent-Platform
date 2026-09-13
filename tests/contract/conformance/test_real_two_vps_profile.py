from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "ci" / "issue562_real_two_vps_conformance.py"
)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _git_commit() -> str:
    completed = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=_repository_root(),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _probe(endpoint: str, scope: str) -> dict[str, object]:
    reachable = scope == "private"
    return {
        "schema": "ai-multi-agent-platform/issue-562-network-probe/v1",
        "status": "pass",
        "observed_at": "2026-09-13T10:00:00+00:00",
        "platform_commit": _git_commit(),
        "source_host_label": "host-b",
        "endpoint_label": endpoint,
        "scope": scope,
        "port": 8765 if endpoint == "message-broker" else 8443,
        "expected": "reachable" if reachable else "closed",
        "reachable": reachable,
        "latency_ms": 8.5 if reachable else 3.0,
        "error_class": None if reachable else "ConnectionRefusedError",
        "outcome": "connected" if reachable else "refused",
        "target_address_recorded": False,
        "credential_material_recorded": False,
    }


def _valid_issue562_report() -> dict[str, object]:
    worker_id = "worker_00000000-0000-4000-8000-000000000562"
    probes = [
        _probe(endpoint, scope)
        for endpoint in ("worker-protocol", "message-broker")
        for scope in ("private", "public")
    ]
    return {
        "schema": "ai-multi-agent-platform/issue-562-two-vps-private-tunnel/v1",
        "status": "pass",
        "observed_at": "2026-09-13T10:00:00+00:00",
        "platform_commit": _git_commit(),
        "deployment_profile": "real-two-vps-private-tunnel",
        "control_host_label": "host-a",
        "worker_host_label": "host-b",
        "canonical": {
            "node_id": "node_00000000-0000-4000-8000-000000000562",
            "worker_id": worker_id,
            "task_id": "task_00000000-0000-4000-8000-000000000562",
            "run_id": "run_00000000-0000-4000-8000-000000000562",
            "worker_job_id": "worker_job_00000000-0000-4000-8000-000000000562",
            "post_recovery_run_id": "run_00000000-0000-4000-8000-000000000563",
            "post_recovery_worker_job_id": (
                "worker_job_00000000-0000-4000-8000-000000000563"
            ),
            "post_restart_run_id": "run_00000000-0000-4000-8000-000000000564",
        },
        "advertised_capability_refs": ["execution:general"],
        "phases": {
            "registration": "pass",
            "dispatch": "pass",
            "interruption": "pass",
            "recovery": "pass",
            "restart": "pass",
            "security": "pass",
        },
        "network": {
            "status": "pass",
            "probes": probes,
            "addresses_recorded": False,
        },
        "transport_evidence": {
            "status": "pass",
            "schema": "ai-multi-agent-platform/issue-388-two-host-transport/v1",
            "worker_id": worker_id,
            "authentication": "mtls",
            "tls": True,
            "artifact_refs": ["artifact_input", "artifact_output"],
            "evidence_refs": ["evidence:issue388-two-host"],
        },
        "conformance": {
            "scenario_id": "E",
            "profile": "real-two-vps-private-tunnel",
            "status": "pass",
            "optional_real_infrastructure": True,
        },
        "credential_material_recorded": False,
        "provider_or_tunnel_identity_canonicalized": False,
    }


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (
            sys.executable,
            str(_SCRIPT),
            "--repository-root",
            str(_repository_root()),
            *args,
        ),
        cwd=_repository_root(),
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _write_and_run(tmp_path: Path, payload: dict[str, object]) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    evidence_path = tmp_path / "issue562.json"
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    report_path = tmp_path / "conformance.json"
    completed = _run(
        "--acceptance-evidence",
        str(evidence_path),
        "--json-report",
        str(report_path),
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return completed, report


def test_profile_without_live_evidence_is_explicitly_unsupported(tmp_path: Path) -> None:
    report_path = tmp_path / "conformance.json"

    completed = _run("--json-report", str(report_path))

    assert completed.returncode == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["deployment_profile"] == "real-two-vps-private-tunnel"
    assert report["passed"] is False
    assert report["compatibility_result"] == "incomplete"
    scenario = report["scenarios"][0]
    assert scenario["scenario_id"] == "ENV-DISTRIBUTED-REAL"
    assert scenario["required"] is True
    assert scenario["status"] == "unsupported"
    assert scenario["compatibility_result"] == "not_claimed"
    assert "simulated Scenario E" in scenario["reason"]


def test_profile_accepts_finalized_live_evidence_from_exact_commit(tmp_path: Path) -> None:
    completed, report = _write_and_run(tmp_path, _valid_issue562_report())

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert report["passed"] is True
    assert report["compatibility_result"] == "compatible"
    scenario = report["scenarios"][0]
    assert scenario["status"] == "pass"
    assert scenario["required"] is True
    assert scenario["canonical_resource_ids"] == [
        "node_00000000-0000-4000-8000-000000000562",
        "worker_00000000-0000-4000-8000-000000000562",
        "task_00000000-0000-4000-8000-000000000562",
        "run_00000000-0000-4000-8000-000000000562",
        "worker_job_00000000-0000-4000-8000-000000000562",
        "run_00000000-0000-4000-8000-000000000563",
        "worker_job_00000000-0000-4000-8000-000000000563",
        "run_00000000-0000-4000-8000-000000000564",
    ]
    assert "issue562:real-two-vps-private-tunnel-report" in scenario["evidence"]
    assert "issue562:transport-artifact-evidence-round-trip" in scenario["evidence"]


def test_profile_rejects_live_evidence_from_different_platform_commit(tmp_path: Path) -> None:
    payload = _valid_issue562_report()
    payload["platform_commit"] = "0" * 40

    completed, report = _write_and_run(tmp_path, payload)

    assert completed.returncode == 1
    scenario = report["scenarios"][0]
    assert scenario["status"] == "fail"
    assert scenario["failure_category"] == "acceptance_failure"
    assert "platform commit does not match" in scenario["stderr"]


def test_profile_rejects_secret_bearing_or_non_sanitized_final_report(tmp_path: Path) -> None:
    payload = _valid_issue562_report()
    payload["credential_material_recorded"] = True

    completed, report = _write_and_run(tmp_path, payload)

    assert completed.returncode == 1
    scenario = report["scenarios"][0]
    assert scenario["status"] == "fail"
    assert "retained credential material" in scenario["stderr"]


def test_profile_rejects_nested_secret_key_even_if_flag_claims_sanitized(tmp_path: Path) -> None:
    payload = _valid_issue562_report()
    payload["unexpected"] = {"token_value": "must-not-survive"}

    completed, report = _write_and_run(tmp_path, payload)

    assert completed.returncode == 1
    scenario = report["scenarios"][0]
    assert scenario["status"] == "fail"
    assert "unsafe evidence key is not allowed" in scenario["stderr"]


def test_profile_rejects_forged_optional_transport_evidence(tmp_path: Path) -> None:
    payload = _valid_issue562_report()
    transport = payload["transport_evidence"]
    assert isinstance(transport, dict)
    transport["tls"] = False

    completed, report = _write_and_run(tmp_path, payload)

    assert completed.returncode == 1
    scenario = report["scenarios"][0]
    assert scenario["status"] == "fail"
    assert "encrypted-transport proof" in scenario["stderr"]


def test_profile_rejects_network_probe_with_wrong_schema(tmp_path: Path) -> None:
    payload = _valid_issue562_report()
    network = payload["network"]
    assert isinstance(network, dict)
    probes = network["probes"]
    assert isinstance(probes, list)
    probe = probes[0]
    assert isinstance(probe, dict)
    probe["schema"] = "forged-network-probe/v0"

    completed, report = _write_and_run(tmp_path, payload)

    assert completed.returncode == 1
    scenario = report["scenarios"][0]
    assert scenario["status"] == "fail"
    assert "network probe uses an unsupported schema" in scenario["stderr"]


def test_profile_rejects_public_probe_that_is_actually_reachable(tmp_path: Path) -> None:
    payload = _valid_issue562_report()
    network = payload["network"]
    assert isinstance(network, dict)
    probes = network["probes"]
    assert isinstance(probes, list)
    public_probe = next(
        probe
        for probe in probes
        if isinstance(probe, dict)
        and probe.get("endpoint_label") == "worker-protocol"
        and probe.get("scope") == "public"
    )
    assert isinstance(public_probe, dict)
    public_probe["reachable"] = True
    public_probe["outcome"] = "connected"

    completed, report = _write_and_run(tmp_path, payload)

    assert completed.returncode == 1
    scenario = report["scenarios"][0]
    assert scenario["status"] == "fail"
    assert "did not prove non-exposure" in scenario["stderr"]


def test_profile_rejects_transport_without_artifact_round_trip(tmp_path: Path) -> None:
    payload = _valid_issue562_report()
    transport = payload["transport_evidence"]
    assert isinstance(transport, dict)
    transport["artifact_refs"] = ["artifact_input"]

    completed, report = _write_and_run(tmp_path, payload)

    assert completed.returncode == 1
    scenario = report["scenarios"][0]
    assert scenario["status"] == "fail"
    assert "artifact_refs is incomplete" in scenario["stderr"]


def test_profile_rejects_non_compact_transport_evidence(tmp_path: Path) -> None:
    payload = _valid_issue562_report()
    transport = payload["transport_evidence"]
    assert isinstance(transport, dict)
    transport["broker_address_recorded"] = False

    completed, report = _write_and_run(tmp_path, payload)

    assert completed.returncode == 1
    scenario = report["scenarios"][0]
    assert scenario["status"] == "fail"
    assert "does not match the compact #388 schema" in scenario["stderr"]
