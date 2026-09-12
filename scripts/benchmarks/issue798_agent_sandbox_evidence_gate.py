#!/usr/bin/env python3
"""Validate completeness and hard security gates for issue #798 live evidence.

The live capture harness intentionally records facts rather than declaring Agent-Sandbox safe.
This companion gate makes that separation machine-readable. It combines the raw Kubernetes/provider
capture with an operator-supplied scenario manifest and refuses to report decision readiness while
required scenarios or representative-environment metadata remain incomplete.

It does *not* choose the final #798 recommendation. A complete campaign may contain failures and
still be decision-ready because `reject/defer` is a valid evidence-based outcome.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeAlias

EVALUATED_REVISION = "d1b7ac007debcb1ba8de91c76afb49bee90d096a"
TERMINAL_SCENARIO_STATUSES = frozenset({"pass", "fail", "unsupported"})
ALLOWED_SCENARIO_STATUSES = TERMINAL_SCENARIO_STATUSES | {"not_run"}
PROFILE_KINDS = frozenset({"upstream-default", "platform-hardened-derivative"})

REQUIRED_SCENARIOS: tuple[str, ...] = (
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

REQUIRED_ENVIRONMENT_FIELDS: tuple[str, ...] = (
    "capture_date",
    "host_class",
    "vcpu",
    "memory_gib",
    "disk_gib",
    "linux_distribution",
    "kernel",
    "kubernetes_distribution",
    "kubernetes_version",
    "container_runtime",
    "runtime_class",
    "agent_sandbox_revision",
    "agent_sandbox_image_digest",
    "sandbox_image_digest",
    "platform_commit",
    "cni",
    "profile_kind",
    "profile_revision",
)

_NUMERIC_ENVIRONMENT_FIELDS = frozenset({"vcpu", "memory_gib", "disk_gib"})
_DIGEST_ENVIRONMENT_FIELDS = frozenset({"agent_sandbox_image_digest", "sandbox_image_digest"})

Status: TypeAlias = Literal["pass", "fail", "not_run"]


@dataclass(frozen=True, slots=True)
class GateResult:
    status: Status
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "reason": self.reason}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _require_issue_identity(evidence: dict[str, Any]) -> None:
    if evidence.get("schema_version") != 1:
        raise ValueError("evidence schema_version must be 1")
    if evidence.get("issue") != 798:
        raise ValueError("evidence issue must be 798")
    if evidence.get("provider") != "agent-sandbox":
        raise ValueError("evidence provider must be agent-sandbox")
    if evidence.get("evaluated_revision") != EVALUATED_REVISION:
        raise ValueError("evidence revision does not match the pinned #798 revision")


def _bool_gate(value: object, *, expected: bool, label: str) -> GateResult:
    if value is None:
        return GateResult("not_run", f"{label} was not captured")
    if not isinstance(value, bool):
        return GateResult("fail", f"{label} has a non-boolean value")
    if value is expected:
        return GateResult("pass", f"{label} matches protected-profile expectation")
    return GateResult("fail", f"{label} violates protected-profile expectation")


def _nonempty_gate(value: object, *, label: str) -> GateResult:
    if value is None or value == "":
        return GateResult("not_run", f"{label} was not captured")
    return GateResult("pass", f"{label} is explicitly configured")


def _image_gate(value: object) -> GateResult:
    if not isinstance(value, str) or not value:
        return GateResult("not_run", "sandbox image was not captured")
    if "@sha256:" not in value:
        return GateResult("fail", "sandbox image is not digest-pinned")
    return GateResult("pass", "sandbox image is digest-pinned")


def _resource_gate(resources: object) -> GateResult:
    if not isinstance(resources, dict):
        return GateResult("not_run", "container resources were not captured")
    requests = resources.get("requests")
    limits = resources.get("limits")
    if not isinstance(requests, dict) or not isinstance(limits, dict):
        return GateResult("fail", "CPU/memory requests and limits are not both configured")
    required = ("cpu", "memory")
    if not all(requests.get(key) and limits.get(key) for key in required):
        return GateResult("fail", "CPU/memory requests and limits are incomplete")
    return GateResult("pass", "CPU/memory requests and limits are configured")


def _network_gate(probe: object, *, label: str) -> GateResult:
    if not isinstance(probe, dict):
        return GateResult("not_run", f"{label} probe was not captured")
    if probe.get("supported") is not True:
        return GateResult("not_run", f"{label} probe was not executable in the sandbox image")
    reachable = probe.get("reachable")
    if reachable is False:
        return GateResult("pass", f"{label} destination is blocked")
    if reachable is True:
        return GateResult("fail", f"{label} destination is reachable")
    return GateResult("not_run", f"{label} reachability is unknown")


def _ambient_paths_gate(probes: object) -> GateResult:
    if not isinstance(probes, list) or not probes:
        return GateResult("not_run", "ambient host-path probes were not captured")
    readable_values = [item.get("readable") for item in probes if isinstance(item, dict)]
    if len(readable_values) != len(probes) or any(value is None for value in readable_values):
        return GateResult("not_run", "ambient host-path probe result is incomplete")
    if any(value is True for value in readable_values):
        return GateResult("fail", "at least one ambient host/runtime path is readable")
    if all(value is False for value in readable_values):
        return GateResult("pass", "captured ambient host/runtime paths are unreadable")
    return GateResult("fail", "ambient host-path probe contains invalid values")


def _canary_gate(value: object) -> GateResult:
    if not isinstance(value, dict) or value.get("performed") is not True:
        return GateResult("not_run", "synthetic secret-canary scan was not performed")
    if value.get("found") is True:
        return GateResult("fail", "synthetic credential canary was retained in workload metadata")
    if value.get("found") is False:
        return GateResult("pass", "synthetic credential canary was absent from scanned metadata")
    return GateResult("not_run", "synthetic secret-canary result is unknown")


def _provider_auth_gate(value: object) -> GateResult:
    if not isinstance(value, dict) or value.get("performed") is not True:
        return GateResult("not_run", "cross-token provider authorization probe was not performed")

    same_tenant = value.get("same_tenant")
    cross_tenant = value.get("cross_tenant")
    if not isinstance(same_tenant, dict) or not isinstance(cross_tenant, dict):
        return GateResult("not_run", "provider authorization direction results are incomplete")

    pairs = {
        "a_to_a": same_tenant.get("a_to_a"),
        "b_to_b": same_tenant.get("b_to_b"),
        "a_to_b": cross_tenant.get("a_to_b"),
        "b_to_a": cross_tenant.get("b_to_a"),
    }
    authorized: dict[str, bool] = {}
    for name, result in pairs.items():
        if not isinstance(result, dict) or not isinstance(result.get("authorized"), bool):
            return GateResult("not_run", f"provider authorization result {name} is unknown")
        authorized[name] = result["authorized"]

    if not authorized["a_to_a"] or not authorized["b_to_b"]:
        return GateResult("fail", "same-token provider GET did not succeed in both directions")
    if authorized["a_to_b"] or authorized["b_to_a"]:
        return GateResult("fail", "cross-token provider GET exposed another sandbox")
    if value.get("cross_tenant_blocked") is not True:
        return GateResult("fail", "provider authorization summary conflicts with direction results")
    return GateResult(
        "pass",
        "same-token provider GET succeeded and cross-token GET was rejected in both directions",
    )


def _hard_gates(evidence: dict[str, Any]) -> dict[str, GateResult]:
    pod = evidence.get("pod_security")
    probes = evidence.get("probes")
    pod = pod if isinstance(pod, dict) else {}
    probes = probes if isinstance(probes, dict) else {}

    seccomp = pod.get("seccomp_profile")
    if seccomp in (None, ""):
        seccomp_gate = GateResult("not_run", "seccomp profile was not captured")
    elif seccomp == "Unconfined":
        seccomp_gate = GateResult("fail", "seccomp profile is Unconfined")
    else:
        seccomp_gate = GateResult("pass", "seccomp profile is explicitly confined")

    return {
        "automount_service_account_token_disabled": _bool_gate(
            pod.get("automount_service_account_token_disabled"),
            expected=True,
            label="ServiceAccount token automount disablement",
        ),
        "host_network_disabled": _bool_gate(
            pod.get("host_network_disabled"), expected=True, label="hostNetwork disablement"
        ),
        "host_pid_disabled": _bool_gate(
            pod.get("host_pid_disabled"), expected=True, label="hostPID disablement"
        ),
        "host_ipc_disabled": _bool_gate(
            pod.get("host_ipc_disabled"), expected=True, label="hostIPC disablement"
        ),
        "allow_privilege_escalation_disabled": _bool_gate(
            pod.get("allow_privilege_escalation_disabled"),
            expected=True,
            label="privilege escalation disablement",
        ),
        "run_as_non_root": _bool_gate(
            pod.get("run_as_non_root"), expected=True, label="non-root execution"
        ),
        "read_only_root_filesystem": _bool_gate(
            pod.get("read_only_root_filesystem"),
            expected=True,
            label="read-only root filesystem",
        ),
        "drops_all_capabilities": _bool_gate(
            pod.get("drops_all_capabilities"), expected=True, label="Linux capability drop ALL"
        ),
        "seccomp_profile": seccomp_gate,
        "runtime_class_name": _nonempty_gate(pod.get("runtime_class_name"), label="runtime class"),
        "digest_pinned_image": _image_gate(pod.get("image")),
        "cpu_memory_bounds": _resource_gate(pod.get("resources")),
        "service_account_token_unreadable": _bool_gate(
            (probes.get("service_account_token") or {}).get("readable")
            if isinstance(probes.get("service_account_token"), dict)
            else None,
            expected=False,
            label="mounted ServiceAccount token readability",
        ),
        "ambient_host_paths_unreadable": _ambient_paths_gate(probes.get("ambient_host_paths")),
        "internet_blocked": _network_gate(probes.get("internet"), label="Internet egress"),
        "metadata_blocked": _network_gate(
            probes.get("metadata"), label="link-local metadata endpoint"
        ),
        "secret_canary_absent": _canary_gate(evidence.get("secret_canary")),
        "cross_token_provider_get_blocked": _provider_auth_gate(
            evidence.get("provider_authorization")
        ),
    }


def _validate_environment(campaign: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    raw = campaign.get("environment")
    if raw is None:
        return {}, list(REQUIRED_ENVIRONMENT_FIELDS)
    if not isinstance(raw, dict):
        raise ValueError("campaign environment must be an object")

    unknown = sorted(set(raw) - set(REQUIRED_ENVIRONMENT_FIELDS))
    if unknown:
        raise ValueError(f"campaign contains unknown environment fields: {', '.join(unknown)}")

    normalized: dict[str, Any] = {}
    missing: list[str] = []
    for name in REQUIRED_ENVIRONMENT_FIELDS:
        value = raw.get(name)
        if value is None or value == "":
            missing.append(name)
            continue
        if name in _NUMERIC_ENVIRONMENT_FIELDS:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"campaign environment {name} must be a positive number")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(f"campaign environment {name} must be a non-empty string")
        normalized[name] = value

    revision = normalized.get("agent_sandbox_revision")
    if revision is not None and revision != EVALUATED_REVISION:
        raise ValueError("campaign environment Agent-Sandbox revision does not match #798 pin")

    profile_kind = normalized.get("profile_kind")
    if profile_kind is not None and profile_kind not in PROFILE_KINDS:
        allowed = ", ".join(sorted(PROFILE_KINDS))
        raise ValueError(f"campaign environment profile_kind must be one of: {allowed}")

    for field in _DIGEST_ENVIRONMENT_FIELDS:
        value = normalized.get(field)
        if value is not None and "sha256:" not in value:
            raise ValueError(f"campaign environment {field} must contain a sha256 digest")

    return normalized, missing


def _environment_evidence_mismatches(
    evidence: dict[str, Any],
    environment: dict[str, Any],
) -> list[str]:
    pod = evidence.get("pod_security")
    pod = pod if isinstance(pod, dict) else {}
    mismatches: list[str] = []

    runtime_class = environment.get("runtime_class")
    captured_runtime_class = pod.get("runtime_class_name")
    if runtime_class is not None and captured_runtime_class is not None:
        if runtime_class != captured_runtime_class:
            mismatches.append("runtime_class")

    sandbox_image = environment.get("sandbox_image_digest")
    captured_image = pod.get("image")
    if sandbox_image is not None and captured_image is not None:
        if sandbox_image != captured_image:
            mismatches.append("sandbox_image_digest")

    return mismatches


def _validate_campaign(campaign: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if campaign.get("schema_version") != 1:
        raise ValueError("campaign schema_version must be 1")
    scenarios = campaign.get("scenarios")
    if not isinstance(scenarios, dict):
        raise ValueError("campaign scenarios must be an object")

    unknown = sorted(set(scenarios) - set(REQUIRED_SCENARIOS))
    if unknown:
        raise ValueError(f"campaign contains unknown scenarios: {', '.join(unknown)}")

    normalized: dict[str, dict[str, Any]] = {}
    for scenario_id in REQUIRED_SCENARIOS:
        raw = scenarios.get(scenario_id)
        if raw is None:
            normalized[scenario_id] = {
                "status": "not_run",
                "evidence": [],
                "reason": "scenario missing from campaign manifest",
            }
            continue
        if not isinstance(raw, dict):
            raise ValueError(f"scenario {scenario_id} must be an object")
        status = raw.get("status")
        if status not in ALLOWED_SCENARIO_STATUSES:
            raise ValueError(f"scenario {scenario_id} has invalid status: {status!r}")
        evidence_refs = raw.get("evidence", [])
        if not isinstance(evidence_refs, list) or not all(
            isinstance(item, str) and item.strip() for item in evidence_refs
        ):
            raise ValueError(f"scenario {scenario_id} evidence must be a list of non-empty strings")
        if status in TERMINAL_SCENARIO_STATUSES and not evidence_refs:
            raise ValueError(
                f"scenario {scenario_id} with status {status!r} requires evidence references"
            )
        normalized[scenario_id] = {
            "status": status,
            "evidence": evidence_refs,
            "reason": raw.get("reason"),
        }
    return normalized


def evaluate(evidence: dict[str, Any], campaign: dict[str, Any]) -> dict[str, Any]:
    _require_issue_identity(evidence)
    hard = _hard_gates(evidence)
    environment, missing_environment = _validate_environment(campaign)
    environment_mismatches = _environment_evidence_mismatches(evidence, environment)
    scenarios = _validate_campaign(campaign)

    hard_failed = sorted(name for name, result in hard.items() if result.status == "fail")
    hard_missing = sorted(name for name, result in hard.items() if result.status == "not_run")
    scenario_failed = sorted(
        name for name, result in scenarios.items() if result["status"] == "fail"
    )
    scenario_unsupported = sorted(
        name for name, result in scenarios.items() if result["status"] == "unsupported"
    )
    scenario_pending = sorted(
        name for name, result in scenarios.items() if result["status"] == "not_run"
    )

    campaign_complete = not scenario_pending
    environment_complete = not missing_environment and not environment_mismatches
    decision_ready = campaign_complete and environment_complete and not hard_missing
    protected_profile_gate = "fail" if hard_failed else "incomplete" if hard_missing else "pass"

    return {
        "schema_version": 1,
        "issue": 798,
        "provider": "agent-sandbox",
        "evaluated_revision": EVALUATED_REVISION,
        "protected_profile_gate": protected_profile_gate,
        "decision_ready": decision_ready,
        "adoption_eligible_from_this_gate": (
            decision_ready and not hard_failed and not scenario_failed and not scenario_unsupported
        ),
        "hard_gates": {name: result.as_dict() for name, result in hard.items()},
        "environment": {
            "complete": environment_complete,
            "metadata": environment,
            "missing": missing_environment,
            "evidence_mismatches": environment_mismatches,
        },
        "campaign": {
            "complete": campaign_complete,
            "scenarios": scenarios,
            "failed": scenario_failed,
            "unsupported": scenario_unsupported,
            "pending": scenario_pending,
        },
        "blockers": {
            "hard_gate_failures": hard_failed,
            "missing_hard_gate_evidence": hard_missing,
            "missing_environment_metadata": missing_environment,
            "environment_evidence_mismatches": environment_mismatches,
            "failed_scenarios": scenario_failed,
            "unsupported_scenarios": scenario_unsupported,
            "pending_scenarios": scenario_pending,
        },
        "interpretation": (
            "decision_ready means the required evaluation campaign, representative-environment "
            "metadata and hard-gate capture are complete and mutually consistent enough to choose "
            "adopt, optional_provider_only, or reject/defer. It does not mean the provider is safe "
            "or adopted. adoption_eligible_from_this_gate is intentionally stricter and becomes "
            "false for any hard-gate failure, failed scenario, unsupported required scenario, "
            "missing or inconsistent representative-environment metadata, or missing hard-gate "
            "evidence."
        ),
    }


def _main() -> int:
    args = _args()
    try:
        report = evaluate(_load_object(args.evidence), _load_object(args.campaign))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"invalid #798 evidence input: {exc}") from exc

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
