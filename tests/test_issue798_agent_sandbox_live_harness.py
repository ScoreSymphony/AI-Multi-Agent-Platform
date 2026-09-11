from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

SCRIPT = Path("scripts/benchmarks/issue798_agent_sandbox_live.py")


def _write_fake_kubectl(
    tmp_path: Path,
    *,
    unsafe: bool = False,
    canary: str | None = None,
) -> Path:
    pod = {
        "metadata": {"creationTimestamp": "2026-09-12T10:00:00Z"},
        "spec": {
            "automountServiceAccountToken": False if not unsafe else True,
            "hostNetwork": False,
            "hostPID": False,
            "hostIPC": False,
            "runtimeClassName": "gvisor" if not unsafe else None,
            "serviceAccountName": "sandbox-untrusted",
            "securityContext": {
                "runAsNonRoot": True,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "sandbox",
                    "image": "example.invalid/sandbox@sha256:abc",
                    "securityContext": {
                        "allowPrivilegeEscalation": False if not unsafe else True,
                        "readOnlyRootFilesystem": True if not unsafe else False,
                        "capabilities": {"drop": ["ALL"] if not unsafe else []},
                    },
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                        "limits": {"cpu": "500m", "memory": "512Mi"},
                    },
                }
            ],
        },
        "status": {
            "conditions": [
                {
                    "type": "Ready",
                    "status": "True",
                    "lastTransitionTime": "2026-09-12T10:00:03Z",
                }
            ],
            "containerStatuses": [
                {"imageID": "containerd://example.invalid/sandbox@sha256:abc"}
            ],
        },
    }
    annotations = {"sandbox-data": "ordinary-evaluation-metadata"}
    if canary is not None:
        annotations["sandbox-data"] += f" secret={canary}"
    replica_sets = {
        "items": [
            {
                "metadata": {
                    "name": "sandbox-rs",
                    "annotations": annotations,
                }
            }
        ]
    }
    cluster = {
        "clientVersion": {"gitVersion": "v1.35.0"},
        "serverVersion": {"gitVersion": "v1.35.0"},
    }

    executable = tmp_path / "kubectl-fake"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"POD = {pod!r}\n"
        f"RS = {replica_sets!r}\n"
        f"CLUSTER = {cluster!r}\n"
        "args = sys.argv[1:]\n"
        "if args[:3] == ['version', '-o', 'json']:\n"
        "    print(json.dumps(CLUSTER)); raise SystemExit(0)\n"
        "if 'auth' in args and 'can-i' in args:\n"
        "    print('Resources  Non-Resource URLs  Resource Names  Verbs')\n"
        "    print('pods       []                 []              [get list]')\n"
        "    raise SystemExit(0)\n"
        "if 'get' in args and 'pod' in args:\n"
        "    print(json.dumps(POD)); raise SystemExit(0)\n"
        "if 'get' in args and 'replicasets' in args:\n"
        "    print(json.dumps(RS)); raise SystemExit(0)\n"
        "if 'exec' in args:\n"
        "    shell = args[-1]\n"
        "    if 'allowed.example' in shell:\n"
        "        raise SystemExit(0)\n"
        "    raise SystemExit(1)\n"
        "print('unexpected args: ' + repr(args), file=sys.stderr)\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | 0o111)
    return executable


def _run(
    tmp_path: Path,
    kubectl: Path,
    *,
    canary: str | None = None,
    allowed_host: str | None = None,
    extra_args: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    output = tmp_path / "evidence.json"
    command = [
        sys.executable,
        str(SCRIPT),
        "--namespace",
        "agent-sandbox-eval",
        "--sandbox-pod",
        "sandbox-a",
        "--output",
        str(output),
        "--kubectl",
        str(kubectl),
    ]
    if canary is not None:
        command.extend(("--canary", canary))
    if allowed_host is not None:
        command.extend(("--allowed-host", allowed_host))
    command.extend(extra_args)
    process_env = os.environ.copy()
    if env is not None:
        process_env.update(env)
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=process_env,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(output.read_text(encoding="utf-8"))


class _TenantAwareHandler(BaseHTTPRequestHandler):
    owners: ClassVar[dict[str, str]] = {
        "sandbox-a": "token-a",
        "sandbox-b": "token-b",
    }
    allow_cross_tenant: ClassVar[bool] = False

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        sandbox_id = self.path.rstrip("/").rsplit("/", 1)[-1]
        token = self.headers.get("X-Api-Key", "")
        allowed = token == self.owners.get(sandbox_id)
        if self.allow_cross_tenant and token in self.owners.values():
            allowed = True
        self.send_response(200 if allowed else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: object) -> None:
        return


def _provider_server(*, allow_cross_tenant: bool) -> tuple[ThreadingHTTPServer, threading.Thread]:
    class Handler(_TenantAwareHandler):
        pass

    Handler.allow_cross_tenant = allow_cross_tenant
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _provider_args(server: ThreadingHTTPServer) -> tuple[str, ...]:
    host, port = server.server_address
    return (
        "--provider-base-url",
        f"http://{host}:{port}/e2b/v1",
        "--sandbox-a-id",
        "sandbox-a",
        "--sandbox-b-id",
        "sandbox-b",
    )


def test_live_harness_captures_protected_profile_evidence(tmp_path: Path) -> None:
    kubectl = _write_fake_kubectl(tmp_path)
    report = _run(tmp_path, kubectl, allowed_host="allowed.example")

    assert report["issue"] == 798
    assert report["provider"] == "agent-sandbox"
    security = report["pod_security"]
    assert security["automount_service_account_token_disabled"] is True
    assert security["allow_privilege_escalation_disabled"] is True
    assert security["run_as_non_root"] is True
    assert security["read_only_root_filesystem"] is True
    assert security["drops_all_capabilities"] is True
    assert security["seccomp_profile"] == "RuntimeDefault"
    assert security["runtime_class_name"] == "gvisor"
    assert security["ready_latency_seconds"] == 3.0
    assert report["probes"]["service_account_token"]["readable"] is False
    assert report["probes"]["internet"]["reachable"] is False
    assert report["probes"]["metadata"]["reachable"] is False
    assert report["probes"]["allowed_host"]["reachable"] is True
    assert report["secret_canary"]["performed"] is False
    assert report["provider_authorization"]["performed"] is False


def test_live_harness_surfaces_unsafe_profile_and_secret_annotation(
    tmp_path: Path,
) -> None:
    canary = "ISSUE798-SYNTHETIC-CANARY"
    kubectl = _write_fake_kubectl(tmp_path, unsafe=True, canary=canary)
    report = _run(tmp_path, kubectl, canary=canary)

    security = report["pod_security"]
    assert security["automount_service_account_token_disabled"] is False
    assert security["allow_privilege_escalation_disabled"] is False
    assert security["read_only_root_filesystem"] is False
    assert security["drops_all_capabilities"] is False
    assert report["secret_canary"] == {
        "performed": True,
        "found": True,
        "matches": ["sandbox-rs"],
    }


def test_live_harness_proves_cross_token_get_is_blocked(tmp_path: Path) -> None:
    kubectl = _write_fake_kubectl(tmp_path)
    server, thread = _provider_server(allow_cross_tenant=False)
    try:
        report = _run(
            tmp_path,
            kubectl,
            extra_args=_provider_args(server),
            env={
                "AGENT_SANDBOX_TOKEN_A": "token-a",
                "AGENT_SANDBOX_TOKEN_B": "token-b",
            },
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    authorization = report["provider_authorization"]
    assert authorization["performed"] is True
    assert authorization["same_tenant"]["a_to_a"]["authorized"] is True
    assert authorization["same_tenant"]["b_to_b"]["authorized"] is True
    assert authorization["cross_tenant"]["a_to_b"]["authorized"] is False
    assert authorization["cross_tenant"]["b_to_a"]["authorized"] is False
    assert authorization["cross_tenant_blocked"] is True
    serialized = json.dumps(report, sort_keys=True)
    assert "token-a" not in serialized
    assert "token-b" not in serialized


def test_live_harness_surfaces_cross_token_get_exposure(tmp_path: Path) -> None:
    kubectl = _write_fake_kubectl(tmp_path)
    server, thread = _provider_server(allow_cross_tenant=True)
    try:
        report = _run(
            tmp_path,
            kubectl,
            extra_args=_provider_args(server),
            env={
                "AGENT_SANDBOX_TOKEN_A": "token-a",
                "AGENT_SANDBOX_TOKEN_B": "token-b",
            },
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    authorization = report["provider_authorization"]
    assert authorization["performed"] is True
    assert authorization["cross_tenant"]["a_to_b"]["authorized"] is True
    assert authorization["cross_tenant"]["b_to_a"]["authorized"] is True
    assert authorization["cross_tenant_blocked"] is False
