#!/usr/bin/env python3
"""Capture reproducible live Agent-Sandbox isolation evidence for issue #798.

This harness is intentionally optional: it requires an already-provisioned Kubernetes
Agent-Sandbox evaluation deployment and never participates in baseline platform operation.
It uses kubectl plus Python's standard library and emits JSON evidence suitable for review.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EVALUATED_REVISION = "d1b7ac007debcb1ba8de91c76afb49bee90d096a"
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SOCKET_PATHS = ("/var/run/docker.sock", "/run/containerd/containerd.sock", "/host")


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class Kubectl:
    def __init__(self, executable: str) -> None:
        self._executable = executable

    def run(self, *args: str, check: bool = True) -> CommandResult:
        completed = subprocess.run(
            (self._executable, *args),
            check=False,
            capture_output=True,
            text=True,
        )
        result = CommandResult(completed.returncode, completed.stdout, completed.stderr)
        if check and result.returncode != 0:
            raise RuntimeError(
                f"kubectl command failed ({result.returncode}): {' '.join(args)}\n{result.stderr}"
            )
        return result

    def json(self, *args: str) -> dict[str, Any]:
        result = self.run(*args)
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise RuntimeError("kubectl JSON response must be an object")
        return payload


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--sandbox-pod", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument("--controller-service-account", default="agent-sandbox")
    parser.add_argument("--canary")
    parser.add_argument("--allowed-host")
    parser.add_argument("--peer-pod-ip")
    parser.add_argument("--internet-host", default="example.com")
    parser.add_argument("--metadata-ip", default="169.254.169.254")
    parser.add_argument("--provider-base-url")
    parser.add_argument("--sandbox-a-id")
    parser.add_argument("--sandbox-b-id")
    parser.add_argument("--token-a-env", default="AGENT_SANDBOX_TOKEN_A")
    parser.add_argument("--token-b-env", default="AGENT_SANDBOX_TOKEN_B")
    return parser.parse_args()


def _parse_time(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _ready_latency_seconds(pod: dict[str, Any]) -> float | None:
    created = _parse_time(pod.get("metadata", {}).get("creationTimestamp"))
    if created is None:
        return None
    for condition in pod.get("status", {}).get("conditions", []):
        if condition.get("type") == "Ready" and condition.get("status") == "True":
            ready = _parse_time(condition.get("lastTransitionTime"))
            if ready is not None:
                return max(0.0, (ready - created).total_seconds())
    return None


def _first_container(pod: dict[str, Any]) -> dict[str, Any]:
    containers = pod.get("spec", {}).get("containers", [])
    if not containers or not isinstance(containers[0], dict):
        raise RuntimeError("sandbox pod has no container spec")
    return containers[0]


def _container_status(pod: dict[str, Any]) -> dict[str, Any]:
    statuses = pod.get("status", {}).get("containerStatuses", [])
    if statuses and isinstance(statuses[0], dict):
        return statuses[0]
    return {}


def _evaluate_pod(pod: dict[str, Any]) -> dict[str, Any]:
    spec = pod.get("spec", {})
    container = _first_container(pod)
    pod_security = spec.get("securityContext") or {}
    container_security = container.get("securityContext") or {}
    seccomp = container_security.get("seccompProfile") or pod_security.get("seccompProfile") or {}
    capabilities = container_security.get("capabilities") or {}
    dropped = capabilities.get("drop") or []
    status = _container_status(pod)

    return {
        "automount_service_account_token_disabled": (
            spec.get("automountServiceAccountToken") is False
        ),
        "host_network_disabled": spec.get("hostNetwork") is not True,
        "host_pid_disabled": spec.get("hostPID") is not True,
        "host_ipc_disabled": spec.get("hostIPC") is not True,
        "allow_privilege_escalation_disabled": (
            container_security.get("allowPrivilegeEscalation") is False
        ),
        "run_as_non_root": container_security.get("runAsNonRoot") is True
        or pod_security.get("runAsNonRoot") is True,
        "read_only_root_filesystem": (container_security.get("readOnlyRootFilesystem") is True),
        "seccomp_profile": seccomp.get("type"),
        "drops_all_capabilities": "ALL" in dropped,
        "runtime_class_name": spec.get("runtimeClassName"),
        "service_account_name": spec.get("serviceAccountName"),
        "image": container.get("image"),
        "image_id": status.get("imageID"),
        "resources": container.get("resources") or {},
        "ready_latency_seconds": _ready_latency_seconds(pod),
    }


def _exec(kubectl: Kubectl, namespace: str, pod: str, shell: str) -> CommandResult:
    return kubectl.run(
        "-n",
        namespace,
        "exec",
        pod,
        "--",
        "sh",
        "-lc",
        shell,
        check=False,
    )


def _probe_readable_path(
    kubectl: Kubectl,
    namespace: str,
    pod: str,
    path: str,
) -> dict[str, Any]:
    result = _exec(kubectl, namespace, pod, f"test -r {path!s}")
    return {
        "path": path,
        "readable": result.returncode == 0,
        "returncode": result.returncode,
    }


def _network_probe(
    kubectl: Kubectl,
    namespace: str,
    pod: str,
    host: str,
    port: int,
) -> dict[str, Any]:
    python = (
        "import socket,sys; "
        f"s=socket.create_connection(({host!r},{port}),2); s.close(); sys.exit(0)"
    )
    shell = (
        "if command -v python3 >/dev/null 2>&1; then python3 -c "
        + json.dumps(python)
        + "; "
        + f"elif command -v nc >/dev/null 2>&1; then nc -z -w 2 {host} {port}; "
        + "else exit 125; fi"
    )
    result = _exec(kubectl, namespace, pod, shell)
    return {
        "host": host,
        "port": port,
        "reachable": result.returncode == 0,
        "supported": result.returncode != 125,
        "returncode": result.returncode,
        "stderr": result.stderr[-1000:],
    }


def _canary_scan(items: dict[str, Any], canary: str | None) -> dict[str, Any]:
    if not canary:
        return {"performed": False, "found": None, "matches": []}
    matches: list[str] = []
    for item in items.get("items", []):
        metadata = item.get("metadata", {})
        name = str(metadata.get("name", "<unknown>"))
        annotations = metadata.get("annotations") or {}
        if canary in json.dumps(annotations, sort_keys=True):
            matches.append(name)
    return {"performed": True, "found": bool(matches), "matches": matches}


def _rbac_capture(
    kubectl: Kubectl,
    namespace: str,
    service_account: str,
) -> dict[str, Any]:
    result = kubectl.run(
        "auth",
        "can-i",
        "--list",
        "-n",
        namespace,
        "--as",
        f"system:serviceaccount:{namespace}:{service_account}",
        check=False,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _cluster_version(kubectl: Kubectl) -> dict[str, Any]:
    result = kubectl.run("version", "-o", "json", check=False)
    if result.returncode != 0:
        return {"available": False, "stderr": result.stderr}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"available": False, "stderr": "kubectl version returned invalid JSON"}
    return {"available": True, "payload": payload}


def _provider_get(base_url: str, sandbox_id: str, token: str) -> dict[str, Any]:
    quoted_id = urllib.parse.quote(sandbox_id, safe="")
    url = f"{base_url.rstrip('/')}/sandboxes/{quoted_id}"
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"X-Api-Key": token, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            return {
                "status": response.status,
                "authorized": 200 <= response.status < 300,
                "transport_error": None,
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": exc.code,
            "authorized": False,
            "transport_error": None,
        }
    except urllib.error.URLError as exc:
        return {
            "status": None,
            "authorized": None,
            "transport_error": type(exc.reason).__name__,
        }


def _provider_authorization_probe(args: argparse.Namespace) -> dict[str, Any]:
    requested = any(
        (
            args.provider_base_url,
            args.sandbox_a_id,
            args.sandbox_b_id,
        )
    )
    if not requested:
        return {"performed": False, "reason": "provider authorization probe not requested"}
    if not all((args.provider_base_url, args.sandbox_a_id, args.sandbox_b_id)):
        return {
            "performed": False,
            "reason": (
                "provider authorization probe requires --provider-base-url, --sandbox-a-id "
                "and --sandbox-b-id"
            ),
        }

    token_a = os.environ.get(args.token_a_env, "")
    token_b = os.environ.get(args.token_b_env, "")
    if not token_a or not token_b:
        return {
            "performed": False,
            "reason": "provider authorization token environment variables are missing",
            "token_env_names": [args.token_a_env, args.token_b_env],
        }

    a_to_a = _provider_get(args.provider_base_url, args.sandbox_a_id, token_a)
    b_to_b = _provider_get(args.provider_base_url, args.sandbox_b_id, token_b)
    a_to_b = _provider_get(args.provider_base_url, args.sandbox_b_id, token_a)
    b_to_a = _provider_get(args.provider_base_url, args.sandbox_a_id, token_b)
    cross_tenant_blocked = a_to_b["authorized"] is False and b_to_a["authorized"] is False

    return {
        "performed": True,
        "token_values_retained": False,
        "same_tenant": {"a_to_a": a_to_a, "b_to_b": b_to_b},
        "cross_tenant": {"a_to_b": a_to_b, "b_to_a": b_to_a},
        "cross_tenant_blocked": cross_tenant_blocked,
        "expected": "same-tenant GET succeeds and cross-token GET is rejected",
    }


def _main() -> int:
    args = _args()
    kubectl = Kubectl(args.kubectl)
    pod = kubectl.json(
        "-n",
        args.namespace,
        "get",
        "pod",
        args.sandbox_pod,
        "-o",
        "json",
    )
    replica_sets = kubectl.json(
        "-n",
        args.namespace,
        "get",
        "replicasets",
        "-o",
        "json",
    )

    probes: dict[str, Any] = {
        "service_account_token": _probe_readable_path(
            kubectl,
            args.namespace,
            args.sandbox_pod,
            TOKEN_PATH,
        ),
        "ambient_host_paths": [
            _probe_readable_path(kubectl, args.namespace, args.sandbox_pod, path)
            for path in SOCKET_PATHS
        ],
        "internet": _network_probe(
            kubectl,
            args.namespace,
            args.sandbox_pod,
            args.internet_host,
            443,
        ),
        "metadata": _network_probe(
            kubectl,
            args.namespace,
            args.sandbox_pod,
            args.metadata_ip,
            80,
        ),
    }
    if args.allowed_host:
        probes["allowed_host"] = _network_probe(
            kubectl,
            args.namespace,
            args.sandbox_pod,
            args.allowed_host,
            443,
        )
    if args.peer_pod_ip:
        probes["peer_sandbox"] = _network_probe(
            kubectl,
            args.namespace,
            args.sandbox_pod,
            args.peer_pod_ip,
            80,
        )

    report = {
        "schema_version": 1,
        "issue": 798,
        "provider": "agent-sandbox",
        "evaluated_revision": EVALUATED_REVISION,
        "captured_at": dt.datetime.now(dt.UTC).isoformat(),
        "environment": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "cluster": _cluster_version(kubectl),
            "namespace": args.namespace,
            "sandbox_pod": args.sandbox_pod,
        },
        "pod_security": _evaluate_pod(pod),
        "controller_rbac": _rbac_capture(
            kubectl,
            args.namespace,
            args.controller_service_account,
        ),
        "secret_canary": _canary_scan(replica_sets, args.canary),
        "provider_authorization": _provider_authorization_probe(args),
        "probes": probes,
        "interpretation": {
            "protected_profile_expected": {
                "service_account_token_readable": False,
                "internet_reachable": False,
                "metadata_reachable": False,
                "ambient_host_paths_readable": False,
                "secret_canary_in_replicaset_annotations": False,
                "cross_tenant_provider_get_blocked": True,
            },
            "note": (
                "This file is raw evaluation evidence. A failed probe is not by itself proof of "
                "complete mediation; DNS, IPv6, redirects, alternate ports/protocols and direct "
                "socket paths still require the full #798 campaign."
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
