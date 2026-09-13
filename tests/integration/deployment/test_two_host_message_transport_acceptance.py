from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "acceptance"
    / "two_host_message_transport.py"
)


def _transport_report(instance_ref: str, worker_job_id: str) -> dict[str, object]:
    input_artifact = f"artifact_input_{worker_job_id}"
    output_artifact = "artifact_output_acceptance"
    evidence_ref = "evidence:issue388-two-host"
    return {
        "schema": "ai-multi-agent-platform/issue-388-two-host-transport/v1",
        "status": "pass",
        "observed_at": "2026-09-13T00:00:00+00:00",
        "control_host_label": "host-a",
        "worker_host_label": "host-b",
        "transport": {
            "provider": "TcpMessageTransport",
            "tls": True,
            "authentication": "mtls",
        },
        "worker_id": "worker_00000000-0000-4000-8000-000000000388",
        "worker_instance_ref": instance_ref,
        "worker_job_id": worker_job_id,
        "run_id": f"run_{worker_job_id}",
        "task_id": f"task_{worker_job_id}",
        "project_id": "project_issue388",
        "correlation_id": f"issue388-two-host:{worker_job_id}",
        "artifact_refs": [input_artifact, output_artifact],
        "evidence_refs": [evidence_ref, instance_ref],
        "expected_input_artifact_ref": input_artifact,
        "expected_output_artifact_ref": output_artifact,
        "expected_evidence_ref": evidence_ref,
        "repeat_dispatch_same_handle": True,
        "broker_address_recorded": False,
        "credential_material_recorded": False,
    }


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_restart_verifier_accepts_same_worker_identity_with_new_process_instance(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    combined = tmp_path / "combined.json"
    _write(first, _transport_report("evidence:transport-worker-instance:first", "job-first"))
    _write(second, _transport_report("evidence:transport-worker-instance:second", "job-second"))

    completed = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "verify-restart",
            str(first),
            str(second),
            "--json-report",
            str(combined),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(combined.read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert report["same_canonical_worker_identity"] is True
    assert report["worker_process_restarted"] is True
    assert report["authenticated_encrypted_transport"] is True
    assert report["artifact_evidence_round_trip"] is True
    assert report["credential_material_recorded"] is False
    assert report["broker_address_recorded"] is False


def test_restart_verifier_rejects_same_process_instance(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    combined = tmp_path / "combined.json"
    shared_instance = "evidence:transport-worker-instance:not-restarted"
    _write(first, _transport_report(shared_instance, "job-first"))
    _write(second, _transport_report(shared_instance, "job-second"))

    completed = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            "verify-restart",
            str(first),
            str(second),
            "--json-report",
            str(combined),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 2
    assert "restart evidence is invalid" in completed.stderr
    assert not combined.exists()
