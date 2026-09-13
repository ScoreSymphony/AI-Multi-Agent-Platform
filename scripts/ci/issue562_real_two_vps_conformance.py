"""Bridge sanitized #562 live evidence into an explicit #46 compatibility report.

This profile is intentionally separate from the simulated Scenario E fixture. It only
claims real two-VPS/private-tunnel compatibility when a finalized #562 report from the
same platform commit is supplied and validates fail-closed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from ai_multi_agent_platform.conformance import (
    ConformanceProfile,
    ConformanceReport,
    ConformanceScenario,
    ConformanceStatus,
    run_conformance,
)
from ai_multi_agent_platform.conformance.evidence import emit_runtime_evidence

ISSUE562_REPORT_SCHEMA = "ai-multi-agent-platform/issue-562-two-vps-private-tunnel/v1"
ISSUE562_PROBE_SCHEMA = "ai-multi-agent-platform/issue-562-network-probe/v1"
ISSUE388_TRANSPORT_SCHEMA = "ai-multi-agent-platform/issue-388-two-host-transport/v1"
DEPLOYMENT_PROFILE = "real-two-vps-private-tunnel"
SCENARIO_ID = "ENV-DISTRIBUTED-REAL"
_REQUIRED_PHASES = {
    "registration",
    "dispatch",
    "interruption",
    "recovery",
    "restart",
    "security",
}
_REQUIRED_CANONICAL_IDS = (
    "node_id",
    "worker_id",
    "task_id",
    "run_id",
    "worker_job_id",
    "post_recovery_run_id",
    "post_recovery_worker_job_id",
    "post_restart_run_id",
)
_REQUIRED_PROBES = {
    ("worker-protocol", "private"),
    ("worker-protocol", "public"),
    ("message-broker", "private"),
    ("message-broker", "public"),
}
_TRANSPORT_KEYS = {
    "status",
    "schema",
    "worker_id",
    "authentication",
    "tls",
    "artifact_refs",
    "evidence_refs",
}
_FORBIDDEN_KEY_PARTS = (
    "password",
    "passwd",
    "private_key",
    "secret_value",
    "token_value",
    "bearer",
    "credential_value",
)


class EvidenceError(ValueError):
    """Raised when finalized real-infrastructure evidence is unsafe or incomplete."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Produce #46 conformance evidence for the optional #562 real two-VPS profile."
    )
    parser.add_argument(
        "--acceptance-evidence",
        type=Path,
        help=(
            "Finalized sanitized #562 acceptance report. Omit it to record the profile as "
            "unsupported/not claimed rather than inferring compatibility from simulated fixtures."
        ),
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="Repository checkout whose exact commit is being claimed.",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        help="Destination for the machine-readable #46 conformance report.",
    )
    parser.add_argument("--probe", action="store_true", help=argparse.SUPPRESS)
    return parser


def _reject_sensitive_keys(value: object, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _FORBIDDEN_KEY_PARTS):
                raise EvidenceError(f"unsafe evidence key is not allowed: {path}.{key}")
            _reject_sensitive_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_keys(child, path=f"{path}[{index}]")


def _load_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"unable to load #562 acceptance evidence: {exc}") from exc
    if not isinstance(payload, dict):
        raise EvidenceError("#562 acceptance evidence must be a JSON object")
    _reject_sensitive_keys(payload, path="issue562-report")
    return payload


def _non_empty_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"#562 acceptance evidence is missing non-empty field {key}")
    return value.strip()


def _mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise EvidenceError(f"#562 acceptance evidence field {key} must be an object")
    return value


def _current_commit(repository_root: Path) -> str:
    try:
        process = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=repository_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise EvidenceError(f"unable to resolve platform commit: {exc}") from exc
    commit = process.stdout.strip()
    if process.returncode != 0 or not commit:
        raise EvidenceError("unable to resolve platform commit for the claimed repository checkout")
    return commit


def _validate_network(
    network: Mapping[str, object],
    *,
    platform_commit: str,
    worker_host_label: str,
) -> None:
    if network.get("status") != "pass":
        raise EvidenceError("#562 network acceptance did not pass")
    if network.get("addresses_recorded") is not False:
        raise EvidenceError("#562 network evidence retained private/public addresses")
    probes = network.get("probes")
    if not isinstance(probes, list):
        raise EvidenceError("#562 network evidence is missing probe results")

    observed: dict[tuple[str, str], Mapping[str, object]] = {}
    for raw_probe in probes:
        if not isinstance(raw_probe, Mapping):
            raise EvidenceError("#562 network probe evidence must contain objects")
        if raw_probe.get("schema") != ISSUE562_PROBE_SCHEMA:
            raise EvidenceError("#562 network probe uses an unsupported schema")
        label = raw_probe.get("endpoint_label")
        scope = raw_probe.get("scope")
        if not isinstance(label, str) or not isinstance(scope, str):
            raise EvidenceError("#562 network probe is missing endpoint label/scope")
        key = (label, scope)
        if key not in _REQUIRED_PROBES:
            raise EvidenceError(f"#562 network probe has unsupported endpoint/scope: {key}")
        if key in observed:
            raise EvidenceError(f"#562 network evidence contains duplicate probe: {key}")
        if raw_probe.get("status") != "pass":
            raise EvidenceError("#562 network evidence contains a non-passing probe")
        if raw_probe.get("platform_commit") != platform_commit:
            raise EvidenceError("#562 network probe uses a different platform commit")
        if raw_probe.get("source_host_label") != worker_host_label:
            raise EvidenceError("#562 network probe was not recorded from the Worker host")
        if raw_probe.get("target_address_recorded") is not False:
            raise EvidenceError("#562 network probe retained a tested address")
        if raw_probe.get("credential_material_recorded") is not False:
            raise EvidenceError("#562 network probe retained credential material")
        observed[key] = raw_probe

    missing = sorted(_REQUIRED_PROBES - observed.keys())
    if missing:
        raise EvidenceError(f"#562 network evidence is missing required probes: {missing}")

    for label in ("worker-protocol", "message-broker"):
        private_probe = observed[(label, "private")]
        public_probe = observed[(label, "public")]
        private_port = private_probe.get("port")
        public_port = public_probe.get("port")
        if (
            isinstance(private_port, bool)
            or not isinstance(private_port, int)
            or private_port <= 0
            or private_port > 65535
        ):
            raise EvidenceError(f"#562 private {label} probe has an invalid service port")
        if public_port != private_port:
            raise EvidenceError(f"#562 public/private {label} probes use different ports")
        if (
            private_probe.get("expected") != "reachable"
            or private_probe.get("reachable") is not True
            or private_probe.get("outcome") != "connected"
        ):
            raise EvidenceError(f"#562 private {label} probe did not prove tunnel reachability")
        if (
            public_probe.get("expected") != "closed"
            or public_probe.get("reachable") is not False
            or public_probe.get("outcome") not in {"refused", "timeout"}
        ):
            raise EvidenceError(f"#562 public {label} probe did not prove non-exposure")


def _validate_canonical_ids(canonical: Mapping[str, object]) -> None:
    values = {key: _non_empty_string(canonical, key) for key in _REQUIRED_CANONICAL_IDS}
    run_ids = {
        values["run_id"],
        values["post_recovery_run_id"],
        values["post_restart_run_id"],
    }
    if len(run_ids) != 3:
        raise EvidenceError("#562 dispatch/recovery/restart evidence reuses a canonical Run ID")
    if values["worker_job_id"] == values["post_recovery_worker_job_id"]:
        raise EvidenceError("#562 recovery evidence reuses the original canonical WorkerJob ID")


def _string_list(
    payload: Mapping[str, object],
    key: str,
    *,
    minimum: int = 1,
) -> list[str]:
    value = payload.get(key)
    if (
        not isinstance(value, list)
        or len(value) < minimum
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise EvidenceError(f"#562 transport evidence field {key} is incomplete")
    normalized = [item.strip() for item in value]
    if len(set(normalized)) != len(normalized):
        raise EvidenceError(f"#562 transport evidence field {key} contains duplicate references")
    return normalized


def _validate_transport(
    payload: Mapping[str, object],
    *,
    worker_id: str,
) -> bool:
    transport = payload.get("transport_evidence")
    if transport is None:
        return False
    if not isinstance(transport, Mapping):
        raise EvidenceError("#562 transport evidence must be an object when present")
    if set(transport.keys()) != _TRANSPORT_KEYS:
        raise EvidenceError("#562 transport evidence does not match the compact #388 schema")
    if transport.get("status") != "pass" or transport.get("schema") != ISSUE388_TRANSPORT_SCHEMA:
        raise EvidenceError("#562 transport evidence is not a passing supported #388 report")
    if transport.get("worker_id") != worker_id:
        raise EvidenceError("#562 transport evidence uses a different canonical Worker")
    if transport.get("tls") is not True:
        raise EvidenceError("#562 transport evidence did not retain encrypted-transport proof")
    authentication = transport.get("authentication")
    if not isinstance(authentication, str) or not authentication.strip():
        raise EvidenceError("#562 transport evidence is missing service authentication")
    _string_list(transport, "artifact_refs", minimum=2)
    _string_list(transport, "evidence_refs")
    return True


def _validate_evidence(path: Path, *, repository_root: Path) -> dict[str, object]:
    payload = _load_json(path)
    if payload.get("schema") != ISSUE562_REPORT_SCHEMA:
        raise EvidenceError("#562 acceptance evidence uses an unsupported schema")
    if payload.get("status") != "pass":
        raise EvidenceError("#562 acceptance evidence is not a passing result")
    if payload.get("deployment_profile") != DEPLOYMENT_PROFILE:
        raise EvidenceError("#562 acceptance evidence uses a different deployment profile")
    if payload.get("credential_material_recorded") is not False:
        raise EvidenceError("#562 acceptance evidence retained credential material")
    if payload.get("provider_or_tunnel_identity_canonicalized") is not False:
        raise EvidenceError("#562 acceptance evidence canonicalized provider/tunnel identity")

    evidence_commit = _non_empty_string(payload, "platform_commit")
    current_commit = _current_commit(repository_root)
    if evidence_commit != current_commit:
        raise EvidenceError(
            "#562 evidence platform commit does not match the repository checkout being claimed"
        )

    control_host = _non_empty_string(payload, "control_host_label")
    worker_host = _non_empty_string(payload, "worker_host_label")
    if control_host == worker_host:
        raise EvidenceError("#562 evidence does not prove two distinct host roles")

    phases = _mapping(payload, "phases")
    missing_phases = sorted(_REQUIRED_PHASES - phases.keys())
    if missing_phases:
        raise EvidenceError(f"#562 evidence is missing required phases: {missing_phases}")
    if any(phases.get(phase) != "pass" for phase in _REQUIRED_PHASES):
        raise EvidenceError("#562 evidence contains a non-passing required phase")

    canonical = _mapping(payload, "canonical")
    _validate_canonical_ids(canonical)

    capabilities = payload.get("advertised_capability_refs")
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or not all(isinstance(value, str) and value.strip() for value in capabilities)
    ):
        raise EvidenceError("#562 evidence is missing advertised Worker capabilities")

    _validate_network(
        _mapping(payload, "network"),
        platform_commit=evidence_commit,
        worker_host_label=worker_host,
    )

    conformance = _mapping(payload, "conformance")
    if conformance.get("scenario_id") != "E":
        raise EvidenceError("#562 evidence is not bound to distributed Scenario E")
    if conformance.get("profile") != DEPLOYMENT_PROFILE:
        raise EvidenceError(
            "#562 conformance evidence uses a different real-infrastructure profile"
        )
    if conformance.get("status") != "pass":
        raise EvidenceError("#562 conformance evidence did not pass")
    if conformance.get("optional_real_infrastructure") is not True:
        raise EvidenceError("#562 evidence is not marked as optional real infrastructure")

    worker_id = _non_empty_string(canonical, "worker_id")
    _validate_transport(payload, worker_id=worker_id)
    return payload


def _probe(acceptance_evidence: Path, *, repository_root: Path) -> int:
    try:
        payload = _validate_evidence(acceptance_evidence, repository_root=repository_root)
    except EvidenceError as exc:
        print(f"real two-VPS conformance evidence rejected: {exc}", file=sys.stderr)
        return 2

    canonical = _mapping(payload, "canonical")
    canonical_ids = [_non_empty_string(canonical, key) for key in _REQUIRED_CANONICAL_IDS]
    evidence_refs = [
        "issue562:real-two-vps-private-tunnel-report",
        "issue562:private-public-network-exposure",
        "issue562:authenticated-registration-heartbeat",
        "issue562:capability-based-remote-dispatch",
        "issue562:tunnel-interruption-recovery",
        "issue562:worker-restart-reregistration",
        "issue562:security-negative-paths",
    ]
    if _validate_transport(payload, worker_id=_non_empty_string(canonical, "worker_id")):
        evidence_refs.append("issue562:transport-artifact-evidence-round-trip")

    emit_runtime_evidence(
        canonical_resource_ids=canonical_ids,
        evidence=evidence_refs,
    )
    return 0


def run_profile(
    *,
    repository_root: Path,
    acceptance_evidence: Path | None,
) -> ConformanceReport:
    script = Path(__file__).resolve()
    if acceptance_evidence is None:
        scenario = ConformanceScenario(
            scenario_id=SCENARIO_ID,
            owner="#562 real distributed acceptance",
            criterion=(
                "two independent VPS hosts pass the private-tunnel distributed runtime, "
                "failure/recovery and security acceptance on the exact claimed platform commit"
            ),
            command=None,
            required=True,
            unavailable_status=ConformanceStatus.UNSUPPORTED,
            unavailable_reason=(
                "no finalized #562 live acceptance report was supplied; simulated Scenario E "
                "evidence cannot establish a real-infrastructure compatibility claim"
            ),
        )
    else:
        evidence_path = acceptance_evidence.resolve()
        scenario = ConformanceScenario(
            scenario_id=SCENARIO_ID,
            owner="#562 real distributed acceptance",
            criterion=(
                "two independent VPS hosts pass the private-tunnel distributed runtime, "
                "failure/recovery and security acceptance on the exact claimed platform commit"
            ),
            command=(
                sys.executable,
                str(script),
                "--acceptance-evidence",
                str(evidence_path),
                "--repository-root",
                str(repository_root.resolve()),
                "--probe",
            ),
            required=True,
            requires_runtime_evidence=True,
        )

    return run_conformance(
        ConformanceProfile.INTEGRATION,
        repository_root=repository_root,
        deployment_profile=DEPLOYMENT_PROFILE,
        scenarios=(scenario,),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository_root = args.repository_root.resolve()
    if args.probe:
        if args.acceptance_evidence is None:
            print("--acceptance-evidence is required for the internal probe", file=sys.stderr)
            return 2
        return _probe(args.acceptance_evidence.resolve(), repository_root=repository_root)

    report = run_profile(
        repository_root=repository_root,
        acceptance_evidence=args.acceptance_evidence,
    )
    if args.json_report is not None:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(report.to_json(), encoding="utf-8")

    print(report.human_summary())
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
