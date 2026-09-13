"""Sanitized operator acceptance harness for issue #562.

The harness prepares and validates evidence for the real two-VPS private-tunnel
acceptance path. It does not provision infrastructure, mutate canonical platform state,
or replace the #14/#35/#36 runtime contracts. Addresses and credential material are
runtime-only inputs and are never written to evidence reports.
"""

from __future__ import annotations

import argparse
import errno
import json
import socket
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

PROBE_SCHEMA = "ai-multi-agent-platform/issue-562-network-probe/v1"
PHASE_SCHEMA = "ai-multi-agent-platform/issue-562-platform-phase/v1"
REPORT_SCHEMA = "ai-multi-agent-platform/issue-562-two-vps-private-tunnel/v1"
TRANSPORT_SCHEMA = "ai-multi-agent-platform/issue-388-two-host-transport/v1"

_ENDPOINT_LABELS = {"worker-protocol", "message-broker"}
_PHASES = {
    "registration",
    "dispatch",
    "interruption",
    "recovery",
    "restart",
    "security",
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
_COMMON_PHASE_KEYS = {
    "command",
    "platform_commit",
    "control_host_label",
    "worker_host_label",
    "json_report",
}
_NETWORK_SETUP_ERRNOS = {
    errno.EADDRNOTAVAIL,
    errno.EHOSTUNREACH,
    errno.ENETDOWN,
    errno.ENETUNREACH,
}


class AcceptanceError(ValueError):
    """Raised when acceptance evidence is incomplete, inconsistent, or unsafe."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _reject_sensitive_keys(value: object, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _FORBIDDEN_KEY_PARTS):
                raise AcceptanceError(f"unsafe evidence key is not allowed: {path}.{key}")
            _reject_sensitive_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_keys(child, path=f"{path}[{index}]")


def _load_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AcceptanceError(f"unable to load JSON evidence {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AcceptanceError(f"evidence must be a JSON object: {path}")
    _reject_sensitive_keys(payload, path=str(path))
    return payload


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AcceptanceError(f"evidence is missing non-empty string field {key}")
    return value


def _required_bool(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise AcceptanceError(f"evidence is missing boolean field {key}")
    return value


def _required_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise AcceptanceError(f"evidence is missing integer field {key}")
    return value


def _bool_arg(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "pass", "passed"}:
        return True
    if lowered in {"0", "false", "no", "fail", "failed"}:
        return False
    raise argparse.ArgumentTypeError("expected true/false")


def _common_record_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--platform-commit", required=True)
    parser.add_argument("--control-host-label", default="host-a")
    parser.add_argument("--worker-host-label", default="host-b")
    parser.add_argument("--json-report", required=True, type=Path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare sanitized issue #562 two-VPS/private-tunnel acceptance evidence."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser(
        "probe",
        help="Run one reachability/non-exposure probe without recording the target address.",
    )
    probe.add_argument("--platform-commit", required=True)
    probe.add_argument("--source-host-label", required=True)
    probe.add_argument("--endpoint-label", choices=sorted(_ENDPOINT_LABELS), required=True)
    probe.add_argument("--scope", choices=("private", "public"), required=True)
    probe.add_argument("--target-address", required=True)
    probe.add_argument("--port", required=True, type=int)
    probe.add_argument("--expect", choices=("reachable", "closed"), required=True)
    probe.add_argument("--timeout-seconds", type=float, default=3.0)
    probe.add_argument("--json-report", required=True, type=Path)

    registration = subparsers.add_parser("record-registration")
    _common_record_arguments(registration)
    registration.add_argument("--node-id", required=True)
    registration.add_argument("--worker-id", required=True)
    registration.add_argument("--authentication-mode", required=True)
    registration.add_argument("--authenticated", type=_bool_arg, required=True)
    registration.add_argument("--heartbeat-healthy", type=_bool_arg, required=True)
    registration.add_argument("--capability", action="append", default=[])
    registration.add_argument("--cpu-cores", type=int, required=True)
    registration.add_argument("--ram-bytes", type=int, required=True)

    dispatch = subparsers.add_parser("record-dispatch")
    _common_record_arguments(dispatch)
    dispatch.add_argument("--worker-id", required=True)
    dispatch.add_argument("--task-id", required=True)
    dispatch.add_argument("--run-id", required=True)
    dispatch.add_argument("--worker-job-id", required=True)
    dispatch.add_argument("--result-status", choices=("succeeded", "failed"), required=True)
    dispatch.add_argument("--correlation-id", required=True)
    dispatch.add_argument("--returned-correlation-id", required=True)
    dispatch.add_argument("--trace-id", required=True)
    dispatch.add_argument("--returned-trace-id", required=True)
    dispatch.add_argument("--duplicate-run-count", type=int, required=True)
    dispatch.add_argument("--selected-by-capability-policy", type=_bool_arg, required=True)

    interruption = subparsers.add_parser("record-interruption")
    _common_record_arguments(interruption)
    interruption.add_argument("--worker-id", required=True)
    interruption.add_argument("--heartbeat-expired", type=_bool_arg, required=True)
    interruption.add_argument("--worker-unavailable", type=_bool_arg, required=True)
    interruption.add_argument("--new-placement-blocked", type=_bool_arg, required=True)
    interruption.add_argument("--active-state-reconciled", type=_bool_arg, required=True)
    interruption.add_argument("--indefinite-running-state", type=_bool_arg, required=True)

    recovery = subparsers.add_parser("record-recovery")
    _common_record_arguments(recovery)
    recovery.add_argument("--worker-id", required=True)
    recovery.add_argument("--re-registered", type=_bool_arg, required=True)
    recovery.add_argument("--heartbeat-recovered", type=_bool_arg, required=True)
    recovery.add_argument("--stale-authority-rejected", type=_bool_arg, required=True)
    recovery.add_argument("--duplicate-run-count", type=int, required=True)
    recovery.add_argument("--post-recovery-run-id", required=True)
    recovery.add_argument("--post-recovery-worker-job-id", required=True)
    recovery.add_argument(
        "--post-recovery-status",
        choices=("succeeded", "failed"),
        required=True,
    )

    restart = subparsers.add_parser("record-restart")
    _common_record_arguments(restart)
    restart.add_argument("--worker-id", required=True)
    restart.add_argument("--first-process-evidence-ref", required=True)
    restart.add_argument("--second-process-evidence-ref", required=True)
    restart.add_argument("--re-registered", type=_bool_arg, required=True)
    restart.add_argument("--heartbeat-recovered", type=_bool_arg, required=True)
    restart.add_argument("--post-restart-run-id", required=True)
    restart.add_argument(
        "--post-restart-status",
        choices=("succeeded", "failed"),
        required=True,
    )

    security = subparsers.add_parser("record-security")
    _common_record_arguments(security)
    security.add_argument("--worker-ports-publicly-closed", type=_bool_arg, required=True)
    security.add_argument(
        "--unauthenticated-registration-rejected",
        type=_bool_arg,
        required=True,
    )
    security.add_argument("--invalid-credential-rejected", type=_bool_arg, required=True)
    security.add_argument("--authorization-server-side", type=_bool_arg, required=True)
    security.add_argument("--reusable-secrets-found", type=_bool_arg, required=True)
    security.add_argument("--provider-metadata-canonicalized", type=_bool_arg, required=True)

    finalize = subparsers.add_parser(
        "finalize",
        help="Validate all sanitized live evidence and produce the compact #562 report.",
    )
    finalize.add_argument("--registration", required=True, type=Path)
    finalize.add_argument("--dispatch", required=True, type=Path)
    finalize.add_argument("--interruption", required=True, type=Path)
    finalize.add_argument("--recovery", required=True, type=Path)
    finalize.add_argument("--restart", required=True, type=Path)
    finalize.add_argument("--security", required=True, type=Path)
    finalize.add_argument("--network-report", action="append", required=True, type=Path)
    finalize.add_argument("--transport-report", type=Path)
    finalize.add_argument("--json-report", required=True, type=Path)
    return parser


def _run_probe(args: argparse.Namespace) -> dict[str, object]:
    if args.port <= 0 or args.port > 65535:
        raise AcceptanceError("--port must be in the TCP port range")
    if args.timeout_seconds <= 0:
        raise AcceptanceError("--timeout-seconds must be greater than zero")

    started = time.perf_counter()
    reachable = False
    outcome = "connected"
    error_class: str | None = None
    error_errno: int | None = None
    try:
        with socket.create_connection(
            (str(args.target_address), int(args.port)),
            timeout=float(args.timeout_seconds),
        ):
            reachable = True
    except socket.gaierror as exc:
        raise AcceptanceError("network probe setup failed: target address did not resolve") from exc
    except TimeoutError as exc:
        outcome = "timeout"
        error_class = type(exc).__name__
        error_errno = exc.errno
    except OSError as exc:
        error_class = type(exc).__name__
        error_errno = exc.errno
        if exc.errno in _NETWORK_SETUP_ERRNOS:
            raise AcceptanceError(
                f"network probe setup failed before endpoint test: {error_class}"
            ) from exc
        if exc.errno == errno.ECONNREFUSED:
            outcome = "refused"
        elif exc.errno == errno.ETIMEDOUT:
            outcome = "timeout"
        else:
            raise AcceptanceError(
                f"network probe failed with unsupported network error: {error_class}"
            ) from exc

    expected_reachable = args.expect == "reachable"
    passed = reachable is expected_reachable
    if args.expect == "closed" and outcome not in {"refused", "timeout"}:
        passed = False
    payload: dict[str, object] = {
        "schema": PROBE_SCHEMA,
        "status": "pass" if passed else "fail",
        "observed_at": _utc_now(),
        "platform_commit": str(args.platform_commit),
        "source_host_label": str(args.source_host_label),
        "endpoint_label": str(args.endpoint_label),
        "scope": str(args.scope),
        "port": int(args.port),
        "expected": str(args.expect),
        "reachable": reachable,
        "outcome": outcome,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "error_class": error_class,
        "error_errno": error_errno,
        "target_address_recorded": False,
        "credential_material_recorded": False,
    }
    _write_json(args.json_report, payload)
    if not passed:
        raise AcceptanceError(
            f"network probe failed for {args.endpoint_label}/{args.scope}: "
            f"expected {args.expect}, reachable={reachable}, outcome={outcome}"
        )
    return payload


def _phase_base(args: argparse.Namespace, phase: str) -> dict[str, object]:
    if args.control_host_label == args.worker_host_label:
        raise AcceptanceError("control and Worker host labels must be distinct")
    return {
        "schema": PHASE_SCHEMA,
        "phase": phase,
        "observed_at": _utc_now(),
        "platform_commit": str(args.platform_commit),
        "control_host_label": str(args.control_host_label),
        "worker_host_label": str(args.worker_host_label),
        "credential_material_recorded": False,
        "network_address_recorded": False,
    }


def _phase_values(args: argparse.Namespace) -> dict[str, object]:
    values: dict[str, object] = {}
    for key, value in vars(args).items():
        if key in _COMMON_PHASE_KEYS:
            continue
        if key == "capability":
            values["capability_refs"] = sorted(set(str(item) for item in value))
        else:
            values[key] = value
    return values


def _phase_passed(command: str, values: Mapping[str, object]) -> bool:
    if command == "record-registration":
        return bool(
            values["authenticated"]
            and values["heartbeat_healthy"]
            and values["capability_refs"]
            and int(values["cpu_cores"]) > 0
            and int(values["ram_bytes"]) > 0
        )
    if command == "record-dispatch":
        return bool(
            values["result_status"] == "succeeded"
            and values["duplicate_run_count"] == 0
            and values["selected_by_capability_policy"]
            and values["correlation_id"] == values["returned_correlation_id"]
            and values["trace_id"] == values["returned_trace_id"]
        )
    if command == "record-interruption":
        return bool(
            values["heartbeat_expired"]
            and values["worker_unavailable"]
            and values["new_placement_blocked"]
            and values["active_state_reconciled"]
            and not values["indefinite_running_state"]
        )
    if command == "record-recovery":
        return bool(
            values["re_registered"]
            and values["heartbeat_recovered"]
            and values["stale_authority_rejected"]
            and values["duplicate_run_count"] == 0
            and values["post_recovery_status"] == "succeeded"
        )
    if command == "record-restart":
        return bool(
            values["first_process_evidence_ref"] != values["second_process_evidence_ref"]
            and values["re_registered"]
            and values["heartbeat_recovered"]
            and values["post_restart_status"] == "succeeded"
        )
    if command == "record-security":
        return bool(
            values["worker_ports_publicly_closed"]
            and values["unauthenticated_registration_rejected"]
            and values["invalid_credential_rejected"]
            and values["authorization_server_side"]
            and not values["reusable_secrets_found"]
            and not values["provider_metadata_canonicalized"]
        )
    raise AcceptanceError(f"unsupported phase command: {command}")


def _record_phase(args: argparse.Namespace) -> dict[str, object]:
    phase = str(args.command).removeprefix("record-")
    values = _phase_values(args)
    if args.command == "record-dispatch":
        values["correlation_preserved"] = (
            values["correlation_id"] == values["returned_correlation_id"]
        )
        values["trace_preserved"] = values["trace_id"] == values["returned_trace_id"]
    if args.command == "record-restart":
        values["worker_process_restarted"] = (
            values["first_process_evidence_ref"] != values["second_process_evidence_ref"]
        )

    passed = _phase_passed(str(args.command), values)
    payload = _phase_base(args, phase)
    payload.update(values)
    payload["status"] = "pass" if passed else "fail"
    _write_json(args.json_report, payload)
    if not passed:
        raise AcceptanceError(f"{phase} evidence records a failing acceptance phase")
    return payload


def _validate_phase(payload: Mapping[str, object], phase: str) -> None:
    if payload.get("schema") != PHASE_SCHEMA:
        raise AcceptanceError(f"{phase} evidence uses an unsupported schema")
    if payload.get("phase") != phase or payload.get("status") != "pass":
        raise AcceptanceError(f"{phase} phase did not pass")
    if payload.get("credential_material_recorded") is not False:
        raise AcceptanceError(f"{phase} evidence may not record credential material")
    if payload.get("network_address_recorded") is not False:
        raise AcceptanceError(f"{phase} evidence may not record network addresses")


def _validate_capabilities(registration: Mapping[str, object]) -> list[str]:
    capability_refs = registration.get("capability_refs")
    if (
        not isinstance(capability_refs, list)
        or not capability_refs
        or not all(isinstance(item, str) and item.strip() for item in capability_refs)
    ):
        raise AcceptanceError("registration evidence must contain advertised capabilities")
    return sorted(set(capability_refs))


def _validate_probes(
    reports: Sequence[Mapping[str, object]],
    *,
    platform_commit: str,
    worker_host_label: str,
) -> list[dict[str, object]]:
    observed: dict[tuple[str, str], Mapping[str, object]] = {}
    safe_reports: list[dict[str, object]] = []
    for report in reports:
        if report.get("schema") != PROBE_SCHEMA or report.get("status") != "pass":
            raise AcceptanceError("network evidence contains a non-passing/unsupported probe")
        if report.get("platform_commit") != platform_commit:
            raise AcceptanceError("network evidence platform commit does not match platform phases")
        if report.get("source_host_label") != worker_host_label:
            raise AcceptanceError("network exposure probes must be recorded from Host B")
        label = _required_string(report, "endpoint_label")
        scope = _required_string(report, "scope")
        if label not in _ENDPOINT_LABELS or scope not in {"private", "public"}:
            raise AcceptanceError("network probe has an unsupported endpoint label/scope")
        key = (label, scope)
        if key in observed:
            raise AcceptanceError(f"duplicate network probe for {label}/{scope}")
        if report.get("target_address_recorded") is not False:
            raise AcceptanceError("network probe must not retain the tested address")
        if report.get("credential_material_recorded") is not False:
            raise AcceptanceError("network probe must not retain credential material")
        observed[key] = report
        safe_reports.append(dict(report))

    required = {(label, scope) for label in _ENDPOINT_LABELS for scope in ("private", "public")}
    missing = sorted(required - observed.keys())
    if missing:
        raise AcceptanceError(f"missing required network probes: {missing}")
    for label in _ENDPOINT_LABELS:
        private_report = observed[(label, "private")]
        public_report = observed[(label, "public")]
        private_port = _required_int(private_report, "port")
        public_port = _required_int(public_report, "port")
        if private_port != public_port:
            raise AcceptanceError(f"{label} public/private probes must test the same service port")
        if (
            private_report.get("expected") != "reachable"
            or private_report.get("reachable") is not True
            or private_report.get("outcome") != "connected"
        ):
            raise AcceptanceError(f"{label} must be reachable through the private tunnel")
        if (
            public_report.get("expected") != "closed"
            or public_report.get("reachable") is not False
            or public_report.get("outcome") not in {"refused", "timeout"}
        ):
            raise AcceptanceError(f"{label} must be closed on the tested public path")
    return safe_reports


def _validate_transport_report(
    report: Mapping[str, object] | None,
    *,
    expected_worker_id: str,
) -> dict[str, object] | None:
    if report is None:
        return None
    if report.get("schema") != TRANSPORT_SCHEMA or report.get("status") != "pass":
        raise AcceptanceError("optional #388 transport evidence is not a passing supported report")
    if report.get("worker_id") != expected_worker_id:
        raise AcceptanceError("#388 transport evidence uses a different canonical Worker")
    transport = report.get("transport")
    if not isinstance(transport, Mapping) or transport.get("tls") is not True:
        raise AcceptanceError("#388 transport evidence is not encrypted")
    authentication = transport.get("authentication")
    if not isinstance(authentication, str) or not authentication:
        raise AcceptanceError("#388 transport evidence is missing service authentication")
    if report.get("broker_address_recorded") is not False:
        raise AcceptanceError("#388 transport evidence retained a broker address")
    if report.get("credential_material_recorded") is not False:
        raise AcceptanceError("#388 transport evidence retained credential material")
    return {
        "status": "pass",
        "schema": TRANSPORT_SCHEMA,
        "worker_id": expected_worker_id,
        "authentication": authentication,
        "tls": True,
        "artifact_refs": report.get("artifact_refs", []),
        "evidence_refs": report.get("evidence_refs", []),
    }


def _same_phase_identity(
    phases: Mapping[str, Mapping[str, object]],
    *,
    platform_commit: str,
    control_host_label: str,
    worker_host_label: str,
) -> None:
    for phase, payload in phases.items():
        if payload.get("platform_commit") != platform_commit:
            raise AcceptanceError(f"{phase} evidence uses a different platform commit")
        if payload.get("control_host_label") != control_host_label:
            raise AcceptanceError(f"{phase} evidence uses a different Control Plane host label")
        if payload.get("worker_host_label") != worker_host_label:
            raise AcceptanceError(f"{phase} evidence uses a different Worker host label")


def _validate_platform_phases(
    phases: Mapping[str, Mapping[str, object]],
    *,
    worker_id: str,
) -> None:
    dispatch = phases["dispatch"]
    interruption = phases["interruption"]
    recovery = phases["recovery"]
    restart = phases["restart"]
    security = phases["security"]

    for phase_name in ("dispatch", "interruption", "recovery", "restart"):
        if phases[phase_name].get("worker_id") != worker_id:
            raise AcceptanceError(f"{phase_name} evidence changed the canonical Worker identity")

    if dispatch.get("result_status") != "succeeded":
        raise AcceptanceError("remote deterministic dispatch did not succeed")
    if _required_int(dispatch, "duplicate_run_count") != 0:
        raise AcceptanceError("normal remote dispatch created duplicate canonical Runs")
    if not _required_bool(dispatch, "selected_by_capability_policy"):
        raise AcceptanceError(
            "Host B was not selected through canonical capability/resource policy"
        )
    if not _required_bool(dispatch, "correlation_preserved"):
        raise AcceptanceError("correlation context did not survive remote dispatch")
    if not _required_bool(dispatch, "trace_preserved"):
        raise AcceptanceError("trace context did not survive remote dispatch")

    interruption_checks = (
        "heartbeat_expired",
        "worker_unavailable",
        "new_placement_blocked",
        "active_state_reconciled",
    )
    if not all(_required_bool(interruption, key) for key in interruption_checks):
        raise AcceptanceError("tunnel interruption evidence is incomplete")
    if _required_bool(interruption, "indefinite_running_state"):
        raise AcceptanceError("Worker disappearance left an indefinitely trusted running state")

    recovery_checks = ("re_registered", "heartbeat_recovered", "stale_authority_rejected")
    if not all(_required_bool(recovery, key) for key in recovery_checks):
        raise AcceptanceError("tunnel recovery/re-registration evidence is incomplete")
    if _required_int(recovery, "duplicate_run_count") != 0:
        raise AcceptanceError("tunnel recovery created duplicate canonical Runs")
    if recovery.get("post_recovery_status") != "succeeded":
        raise AcceptanceError("post-recovery deterministic dispatch did not succeed")

    if not _required_bool(restart, "worker_process_restarted"):
        raise AcceptanceError("Worker restart evidence did not prove a new process instance")
    if not _required_bool(restart, "re_registered"):
        raise AcceptanceError("Worker restart did not re-register")
    if not _required_bool(restart, "heartbeat_recovered"):
        raise AcceptanceError("Worker restart did not recover heartbeat")
    if restart.get("post_restart_status") != "succeeded":
        raise AcceptanceError("post-restart deterministic dispatch did not succeed")

    original_run_id = _required_string(dispatch, "run_id")
    recovery_run_id = _required_string(recovery, "post_recovery_run_id")
    restart_run_id = _required_string(restart, "post_restart_run_id")
    if len({original_run_id, recovery_run_id, restart_run_id}) != 3:
        raise AcceptanceError("dispatch, recovery, and restart must use distinct canonical Run IDs")
    original_worker_job_id = _required_string(dispatch, "worker_job_id")
    recovery_worker_job_id = _required_string(recovery, "post_recovery_worker_job_id")
    if original_worker_job_id == recovery_worker_job_id:
        raise AcceptanceError("post-recovery dispatch must use a fresh canonical WorkerJob ID")

    security_checks = (
        "worker_ports_publicly_closed",
        "unauthenticated_registration_rejected",
        "invalid_credential_rejected",
        "authorization_server_side",
    )
    if not all(_required_bool(security, key) for key in security_checks):
        raise AcceptanceError("security acceptance evidence is incomplete")
    if _required_bool(security, "reusable_secrets_found"):
        raise AcceptanceError("sanitized evidence reported reusable secret material")
    if _required_bool(security, "provider_metadata_canonicalized"):
        raise AcceptanceError("provider/tunnel metadata leaked into canonical identity")


def _finalize(args: argparse.Namespace) -> dict[str, object]:
    phase_paths = {
        "registration": args.registration,
        "dispatch": args.dispatch,
        "interruption": args.interruption,
        "recovery": args.recovery,
        "restart": args.restart,
        "security": args.security,
    }
    phases = {phase: _load_json(path) for phase, path in phase_paths.items()}
    for phase, payload in phases.items():
        _validate_phase(payload, phase)

    registration = phases["registration"]
    platform_commit = _required_string(registration, "platform_commit")
    control_host_label = _required_string(registration, "control_host_label")
    worker_host_label = _required_string(registration, "worker_host_label")
    if control_host_label == worker_host_label:
        raise AcceptanceError("final evidence does not contain two distinct host labels")
    _same_phase_identity(
        phases,
        platform_commit=platform_commit,
        control_host_label=control_host_label,
        worker_host_label=worker_host_label,
    )

    node_id = _required_string(registration, "node_id")
    worker_id = _required_string(registration, "worker_id")
    if not _required_bool(registration, "authenticated"):
        raise AcceptanceError("Worker registration was not authenticated")
    if not _required_bool(registration, "heartbeat_healthy"):
        raise AcceptanceError("Worker heartbeat was not healthy before dispatch")
    capability_refs = _validate_capabilities(registration)
    _validate_platform_phases(phases, worker_id=worker_id)

    dispatch = phases["dispatch"]
    recovery = phases["recovery"]
    restart = phases["restart"]
    probes = [_load_json(path) for path in args.network_report]
    safe_probes = _validate_probes(
        probes,
        platform_commit=platform_commit,
        worker_host_label=worker_host_label,
    )
    transport_report = _load_json(args.transport_report) if args.transport_report else None
    transport_evidence = _validate_transport_report(
        transport_report,
        expected_worker_id=worker_id,
    )

    report: dict[str, object] = {
        "schema": REPORT_SCHEMA,
        "status": "pass",
        "observed_at": _utc_now(),
        "platform_commit": platform_commit,
        "deployment_profile": "real-two-vps-private-tunnel",
        "control_host_label": control_host_label,
        "worker_host_label": worker_host_label,
        "canonical": {
            "node_id": node_id,
            "worker_id": worker_id,
            "task_id": _required_string(dispatch, "task_id"),
            "run_id": _required_string(dispatch, "run_id"),
            "worker_job_id": _required_string(dispatch, "worker_job_id"),
            "post_recovery_run_id": _required_string(recovery, "post_recovery_run_id"),
            "post_recovery_worker_job_id": _required_string(
                recovery,
                "post_recovery_worker_job_id",
            ),
            "post_restart_run_id": _required_string(restart, "post_restart_run_id"),
        },
        "advertised_capability_refs": capability_refs,
        "phases": {phase: "pass" for phase in sorted(_PHASES)},
        "network": {
            "status": "pass",
            "probes": safe_probes,
            "addresses_recorded": False,
        },
        "transport_evidence": transport_evidence,
        "conformance": {
            "scenario_id": "E",
            "profile": "real-two-vps-private-tunnel",
            "status": "pass",
            "optional_real_infrastructure": True,
        },
        "credential_material_recorded": False,
        "provider_or_tunnel_identity_canonicalized": False,
    }
    _reject_sensitive_keys(report, path="final-report")
    _write_json(args.json_report, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "probe":
            _run_probe(args)
        elif args.command == "finalize":
            _finalize(args)
        else:
            _record_phase(args)
    except AcceptanceError as exc:
        print(f"issue #562 acceptance failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
