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


def _environment() -> dict[str, object]:
    return {
        "capture_date": "2026-09-12",
        "host_class": "representative-vps",
        "vcpu": 8,
        "memory_gib": 32,
        "disk_gib": 200,
        "linux_distribution": "Ubuntu 24.04",
        "kernel": "6.8.0",
        "kubernetes_distribution": "k3s",
        "kubernetes_version": "v1.35.0",
        "container_runtime": "containerd 2.x",
        "runtime_class": "gvisor",
        "agent_sandbox_revision": REVISION,
        "agent_sandbox_image_digest": "example.invalid/agent-sandbox@sha256:def",
        "sandbox_image_digest": "example.invalid/sandbox@sha256:abc",
        "platform_commit": "0123456789abcdef0123456789abcdef01234567",
        "cni": "cilium",
        "profile_kind": "platform-hardened-derivative",
        "profile_revision": "issue798-eval-profile-v1",
    }


def _authorization(*, same_tenant_allowed: bool = True) -> dict[str, object]:
    return {
        "performed": True,
        "same_tenant": {
            "a_to_a": {"authorized": same_tenant_allowed},
            "b_to_b": {"authorized": same_tenant_allowed},
        },
        "cross_tenant": {
            "a_to_b": {"authorized": False},
            "b_to_a": {"authorized": False},
        },
        "cross_tenant_blocked": True,
    }


def _evidence(*, unsafe: bool = False, complete: bool = True) -> dict[str, object]:
    return {
        "schema_version": 1,
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
        "provider_authorization": _authorization() if complete else {"performed": False},
    }


def _campaign(
    *,
    status: str = "pass",
    overrides: dict[str, str] | None = None,
    environment: dict[str, object] | None = None,
) -> dict[str, object]:
    statuses = {scenario: status for scenario in REQUIRED_SCENARIOS}
    if overrides:
        statuses.update(overrides)
    return {
        "schema_version": 1,
        "environment": _environment() if environment is None else environment,
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
    assert "host_class" in blockers["missing_environment_metadata"]
    assert "runtime_class" in blockers["missing_environment_metadata"]
    assert set(blockers["pending_scenarios"]) == set(REQUIRED_SCENARIOS)


def test_gate_surfaces_unsafe_profile_without_blocking_reject_decision(tmp_path: Path) -> None:
    environment = _environment()
    environment["sandbox_image_digest"] = "example.invalid/sandbox:latest"
    completed, report = _run(
        tmp_path,
        evidence=_evidence(unsafe=True),
        campaign=_campaign(environment=environment),
    )

    assert completed.returncode != 0
    assert report is None
    assert "must contain a sha256 digest" in completed.stderr


def test_gate_allows_measured_unsafe_profile_to_support_reject_decision(tmp_path: Path) -> None:
    evidence = _evidence()
    pod_security = evidence["pod_security"]
    assert isinstance(pod_security, dict)
    pod_security["automount_service_account_token_disabled"] = False
    pod_security["allow_privilege_escalation_disabled"] = False
    probes = evidence["probes"]
    assert isinstance(probes, dict)
    internet = probes["internet"]
    assert isinstance(internet, dict)
    internet["reachable"] = True

    completed, report = _run(
        tmp_path,
        evidence=evidence,
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
    assert report["environment"]["complete"] is True
    assert report["campaign"]["complete"] is True
    assert report["blockers"] == {
        "hard_gate_failures": [],
        "missing_hard_gate_evidence": [],
        "missing_environment_metadata": [],
        "environment_evidence_mismatches": [],
        "failed_scenarios": [],
        "unsupported_scenarios": [],
        "pending_scenarios": [],
    }


def test_gate_requires_representative_metadata_for_decision_readiness(tmp_path: Path) -> None:
    completed, report = _run(
        tmp_path,
        evidence=_evidence(),
        campaign=_campaign(environment={}),
    )

    assert completed.returncode == 0, completed.stderr
    assert report is not None
    assert report["protected_profile_gate"] == "pass"
    assert report["campaign"]["complete"] is True
    assert report["environment"]["complete"] is False
    assert report["decision_ready"] is False
    assert report["adoption_eligible_from_this_gate"] is False
    missing = report["blockers"]["missing_environment_metadata"]
    assert "host_class" in missing
    assert "vcpu" in missing
    assert "cni" in missing
    assert "profile_revision" in missing


def test_gate_rejects_environment_that_does_not_match_raw_evidence(tmp_path: Path) -> None:
    environment = _environment()
    environment["runtime_class"] = "kata"

    completed, report = _run(
        tmp_path,
        evidence=_evidence(),
        campaign=_campaign(environment=environment),
    )

    assert completed.returncode == 0, completed.stderr
    assert report is not None
    assert report["protected_profile_gate"] == "pass"
    assert report["environment"]["complete"] is False
    assert report["decision_ready"] is False
    assert report["adoption_eligible_from_this_gate"] is False
    assert report["blockers"]["environment_evidence_mismatches"] == ["runtime_class"]


def test_gate_rejects_block_everything_as_tenant_isolation_success(tmp_path: Path) -> None:
    evidence = _evidence()
    evidence["provider_authorization"] = _authorization(same_tenant_allowed=False)

    completed, report = _run(
        tmp_path,
        evidence=evidence,
        campaign=_campaign(),
    )

    assert completed.returncode == 0, completed.stderr
    assert report is not None
    assert report["protected_profile_gate"] == "fail"
    assert report["decision_ready"] is True
    assert report["adoption_eligible_from_this_gate"] is False
    assert report["hard_gates"]["cross_token_provider_get_blocked"]["status"] == "fail"


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


def test_gate_rejects_campaign_with_wrong_upstream_revision(tmp_path: Path) -> None:
    environment = _environment()
    environment["agent_sandbox_revision"] = "different-revision"

    completed, report = _run(
        tmp_path,
        evidence=_evidence(),
        campaign=_campaign(environment=environment),
    )

    assert completed.returncode != 0
    assert report is None
    assert "does not match #798 pin" in completed.stderr


def test_gate_rejects_raw_evidence_without_schema_version(tmp_path: Path) -> None:
    evidence = _evidence()
    del evidence["schema_version"]

    completed, report = _run(
        tmp_path,
        evidence=evidence,
        campaign=_campaign(),
    )

    assert completed.returncode != 0
    assert report is None
    assert "evidence schema_version must be 1" in completed.stderr
