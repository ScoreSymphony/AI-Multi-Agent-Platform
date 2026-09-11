from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path("scripts/benchmarks/issue798_agent_sandbox_evidence_gate.py")
REVISION = "d1b7ac007debcb1ba8de91c76afb49bee90d096a"
REQUIRED_SCENARIOS = (
    "benign_shell_artifact_roundtrip",
    "cpu_runaway",
    "memory_runaway_oom",
    "timeout_cancel_cleanup",
    "workspace_escape",
    "egress_bypass_matrix",
    "internal_service_isolation",
    "scoped_credential_delivery",
    "provider_object_ownership_all_operations",
    "browser_artifact_roundtrip",
    "pause_resume_process_state",
    "snapshot_restore_compatibility",
    "crash_restart_cleanup",
    "cold_warm_start_repetitions",
    "idle_resource_footprint",
    "concurrent_density",
    "multi_sandbox_concurrency_cleanup",
    "malicious_repository_fixture",
)


def _evidence(*, unsafe: bool = False, complete: bool = True) -> dict[str, object]:
    return {
        "issue": 798,
        "provider": "agent-sandbox",
        "evaluated_revision": REVISION,
        "pod_security": {
            "automount_service_account_token_disabled": not unsafe,
            "host_network_disabled": True,
            "host_pid_disabled": True,
            "host_ipc_disabled": True,
            "allow_privilege_escalation_disabled": not unsafe,
            "run_as_non_root": True,
            "read_only_root_filesystem": True,
            "drops_all_capabilities": True,
            "seccomp_profile": "RuntimeDefault",
            "runtime_class_name": "gvisor",
            "image": (
                "example.invalid/sandbox:latest"
                if unsafe
                else "example.invalid/sandbox@sha256:abc"
            ),
            "resources": {
                "requests": {"cpu": "100m", "memory": "128Mi"},
                "limits": {"cpu": "500m", "memory": "512Mi"},
            },
        },
        "probes": {
            "service_account_token": {"readable": False},
            "ambient_host_paths": [
                {"path": "/var/run/docker.sock", "readable": False},
                {"path": "/run/containerd/containerd.sock", "readable": False},
                {"path": "/host", "readable": False},
            ],
            "internet": {"supported": True, "reachable": unsafe},
            "metadata": {"supported": True, "reachable": False},
        },
        "secret_canary": (
            {"performed": True, "found": False, "matches": []}
            if complete
            else {"performed": False, "found": None, "matches": []}
        ),
        "provider_authorization": (
            {"performed": True, "cross_tenant_blocked": True}
            if complete
            else {"performed": False}
        ),
    }


def _campaign(
    *,
    status: str = "pass",
    overrides: dict[str, str] | None = None,
) -> dict[str, object]:
    statuses = {scenario: status for scenario in REQUIRED_SCENARIOS}
    if overrides:
        statuses.update(overrides)
    return {
        "schema_version": 1,
        "scenarios": {
            scenario: {
                "status": scenario_status,
                "evidence": (
                    []
                    if scenario_status == "not_run"
                    else [f"evidence/{scenario}.json"]
                ),
            }
            for scenario, scenario_status in statuses.items()
        },
    }


def _run(
    tmp_path: Path,
    *,
    evidence: dict[str, object],
    campaign: dict[str, object],
) -> tuple[subprocess.CompletedProcess[str], dict[str, object] | None]:
    evidence_path = tmp_path / "evidence.json"
    campaign_path = tmp_path / "campaign.json"
    output_path = tmp_path / "gate.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    campaign_path.write_text(json.dumps(campaign), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--evidence",
            str(evidence_path),
            "--campaign",
            str(campaign_path),
            "--output",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if not output_path.exists():
        return completed, None
    return completed, json.loads(output_path.read_text(encoding="utf-8"))


def test_gate_keeps_missing_live_evidence_and_scenarios_incomplete(tmp_path: Path) -> None:
    completed, report = _run(
        tmp_path,
        evidence=_evidence(complete=False),
        campaign={"schema_version": 1, "scenarios": {}},
    )

    assert completed.returncode == 0, completed.stderr
    assert report is not None
    assert report["protected_profile_gate"] == "incomplete"
    assert report["decision_ready"] is False
    assert report["adoption_eligible_from_this_gate"] is False
    blockers = report["blockers"]
    assert blockers["missing_hard_gate_evidence"] == [
        "cross_token_provider_get_blocked",
        "secret_canary_absent",
    ]
    assert set(blockers["pending_scenarios"]) == set(REQUIRED_SCENARIOS)


def test_gate_surfaces_unsafe_profile_without_blocking_reject_decision(tmp_path: Path) -> None:
    completed, report = _run(
        tmp_path,
        evidence=_evidence(unsafe=True),
        campaign=_campaign(),
    )

    assert completed.returncode == 0, completed.stderr
    assert report is not None
    assert report["protected_profile_gate"] == "fail"
    assert report["decision_ready"] is True
    assert report["adoption_eligible_from_this_gate"] is False
    failures = report["blockers"]["hard_gate_failures"]
    assert "automount_service_account_token_disabled" in failures
    assert "allow_privilege_escalation_disabled" in failures
    assert "digest_pinned_image" in failures
    assert "internet_blocked" in failures


def test_gate_reports_complete_passing_campaign_as_adoption_eligible(tmp_path: Path) -> None:
    completed, report = _run(
        tmp_path,
        evidence=_evidence(),
        campaign=_campaign(),
    )

    assert completed.returncode == 0, completed.stderr
    assert report is not None
    assert report["protected_profile_gate"] == "pass"
    assert report["decision_ready"] is True
    assert report["adoption_eligible_from_this_gate"] is True
    assert report["campaign"]["complete"] is True
    assert report["blockers"] == {
        "hard_gate_failures": [],
        "missing_hard_gate_evidence": [],
        "failed_scenarios": [],
        "unsupported_scenarios": [],
        "pending_scenarios": [],
    }


def test_gate_allows_unsupported_result_for_decision_but_not_adoption(tmp_path: Path) -> None:
    completed, report = _run(
        tmp_path,
        evidence=_evidence(),
        campaign=_campaign(overrides={"snapshot_restore_compatibility": "unsupported"}),
    )

    assert completed.returncode == 0, completed.stderr
    assert report is not None
    assert report["decision_ready"] is True
    assert report["adoption_eligible_from_this_gate"] is False
    assert report["blockers"]["unsupported_scenarios"] == [
        "snapshot_restore_compatibility"
    ]


def test_gate_rejects_terminal_scenario_without_evidence_reference(tmp_path: Path) -> None:
    campaign = _campaign()
    scenarios = campaign["scenarios"]
    assert isinstance(scenarios, dict)
    scenario = scenarios["cpu_runaway"]
    assert isinstance(scenario, dict)
    scenario["evidence"] = []

    completed, report = _run(
        tmp_path,
        evidence=_evidence(),
        campaign=campaign,
    )

    assert completed.returncode != 0
    assert report is None
    assert "requires evidence references" in completed.stderr
