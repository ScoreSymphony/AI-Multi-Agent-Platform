#!/usr/bin/env python3
"""Reproducible live evidence harness for issue #861.

This script is intentionally outside the platform package. It imports the pinned
SWE-ReX installation only when executed and records backend behavior without
making SWE-ReX a platform dependency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from time import monotonic
from typing import Any

PINNED_REVISION = "5c995c365dfb1fd5bc56fda688be5d8538f9931f"
EXPECTED_VERSION = "1.4.0"
SYNTHETIC_SECRET = "issue-861-synthetic-canary"


def _command_payload() -> str:
    return (
        "import os,sys; "
        "print('stdout-ok'); "
        "print('stderr-ok', file=sys.stderr); "
        "print(os.environ.get('ISSUE861_SECRET', 'missing'))"
    )


def _environment_probe_payload() -> str:
    return "import os; print(os.environ.get('ISSUE861_SECRET', 'missing'))"


def _timeout_payload() -> str:
    return "import time; time.sleep(2); print('unexpected-timeout-completion')"


def _timeout_child_payload() -> str:
    return (
        "import subprocess,sys,time; "
        'child_code="import pathlib,sys,time; time.sleep(0.25); '
        "pathlib.Path(sys.argv[1]).write_text('survived')\"; "
        "subprocess.Popen([sys.executable,'-c',child_code,sys.argv[1]], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True); "
        "time.sleep(2)"
    )


def _egress_payload() -> str:
    return (
        "import urllib.error\n"
        "import urllib.request\n"
        "try:\n"
        "    response = urllib.request.urlopen('http://example.com', timeout=5)\n"
        "    print(response.status)\n"
        "except urllib.error.HTTPError as exc:\n"
        "    print(exc.code)\n"
    )


async def _run_local() -> dict[str, Any]:
    import swerex
    from swerex.deployment.local import LocalDeployment
    from swerex.runtime.abstract import Command, ReadFileRequest, WriteFileRequest

    evidence: dict[str, Any] = {
        "backend": "local",
        "swerex_version": swerex.__version__,
        "expected_version": EXPECTED_VERSION,
        "pinned_revision": PINNED_REVISION,
        "host_platform": platform.platform(),
        "python": sys.version,
        "local_isolation_claim": "none",
    }
    deployment = LocalDeployment()
    started = monotonic()
    await deployment.start()
    evidence["startup_seconds"] = monotonic() - started
    try:
        with tempfile.TemporaryDirectory(prefix="issue861-") as temp_dir:
            root = Path(temp_dir).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside.txt"
            outside.write_text("outside-canary", encoding="utf-8")

            response = await deployment.runtime.execute(
                Command(
                    command=[sys.executable, "-c", _command_payload()],
                    cwd=str(workspace),
                    env={**os.environ, "ISSUE861_SECRET": SYNTHETIC_SECRET},
                )
            )
            evidence["execute"] = {
                "exit_code": response.exit_code,
                "stdout_has_marker": "stdout-ok" in response.stdout,
                "stderr_has_marker": "stderr-ok" in response.stderr,
                "synthetic_env_visible": SYNTHETIC_SECRET in response.stdout,
            }

            followup = await deployment.runtime.execute(
                Command(
                    command=[sys.executable, "-c", _environment_probe_payload()],
                    cwd=str(workspace),
                    check=False,
                )
            )
            evidence["synthetic_env_persisted_after_command"] = SYNTHETIC_SECRET in followup.stdout

            provider_file = workspace / "provider.txt"
            await deployment.runtime.write_file(
                WriteFileRequest(path=str(provider_file), content="artifact-canary")
            )
            read_back = await deployment.runtime.read_file(
                ReadFileRequest(path=str(provider_file), encoding="utf-8")
            )
            evidence["file_round_trip"] = read_back.content == "artifact-canary"

            outside_read = await deployment.runtime.read_file(
                ReadFileRequest(path=str(outside), encoding="utf-8")
            )
            evidence["outside_temp_workspace_read_succeeded"] = (
                outside_read.content == "outside-canary"
            )

            timeout_started = monotonic()
            try:
                timeout_response = await deployment.runtime.execute(
                    Command(
                        command=[sys.executable, "-c", _timeout_payload()],
                        cwd=str(workspace),
                        timeout=0.05,
                    )
                )
                evidence["timeout"] = {
                    "raised": False,
                    "exit_code": timeout_response.exit_code,
                    "elapsed_seconds": monotonic() - timeout_started,
                }
            except Exception as exc:
                evidence["timeout"] = {
                    "raised": True,
                    "exception_type": type(exc).__name__,
                    "elapsed_seconds": monotonic() - timeout_started,
                }

            child_marker = workspace / "child-after-timeout.txt"
            try:
                await deployment.runtime.execute(
                    Command(
                        command=[
                            sys.executable,
                            "-c",
                            _timeout_child_payload(),
                            str(child_marker),
                        ],
                        cwd=str(workspace),
                        timeout=0.05,
                    )
                )
            except Exception as exc:
                evidence["timeout_child_cleanup"] = {
                    "parent_timeout_exception": type(exc).__name__,
                }
            else:
                evidence["timeout_child_cleanup"] = {
                    "parent_timeout_exception": None,
                }
            await asyncio.sleep(0.4)
            evidence["timeout_child_cleanup"]["child_survived_parent_timeout"] = (
                child_marker.exists()
            )
    finally:
        stopped = monotonic()
        await deployment.stop()
        evidence["stop_seconds"] = monotonic() - stopped

    evidence["passed_core_semantics"] = bool(
        evidence["swerex_version"] == EXPECTED_VERSION
        and evidence["execute"]["exit_code"] == 0
        and evidence["execute"]["stdout_has_marker"]
        and evidence["execute"]["stderr_has_marker"]
        and evidence["file_round_trip"]
        and evidence["outside_temp_workspace_read_succeeded"]
    )
    return evidence


async def _run_docker(*, backend: str) -> dict[str, Any]:
    import swerex
    from swerex.deployment.docker import DockerDeployment
    from swerex.runtime.abstract import Command, ReadFileRequest, UploadRequest

    internal_network = backend == "docker-internal"
    docker_args: list[str] = []
    if internal_network:
        network_name = os.environ.get("ISSUE861_DOCKER_NETWORK")
        if not network_name:
            raise RuntimeError("ISSUE861_DOCKER_NETWORK is required for docker-internal evidence")
        docker_args = [f"--network={network_name}"]

    configured_image = os.environ.get("ISSUE861_SWEREX_IMAGE")
    docker_image = configured_image or "python:3.11"
    evidence: dict[str, Any] = {
        "backend": backend,
        "swerex_version": swerex.__version__,
        "expected_version": EXPECTED_VERSION,
        "pinned_revision": PINNED_REVISION,
        "host_platform": platform.platform(),
        "python": sys.version,
        "docker_args": docker_args,
        "docker_image": docker_image,
        "docker_image_explicitly_pinned": configured_image is not None,
    }
    deployment = DockerDeployment(
        image=docker_image,
        pull="never" if configured_image is not None else "missing",
        docker_args=docker_args,
        remove_container=True,
    )
    started = monotonic()
    await deployment.start()
    evidence["startup_seconds"] = monotonic() - started
    try:
        container_name = deployment.container_name
        if container_name is not None:
            port_probe = subprocess.run(
                ["docker", "port", container_name, "8000/tcp"],
                check=False,
                capture_output=True,
                text=True,
            )
            evidence["published_port_bindings"] = [
                line for line in port_probe.stdout.splitlines() if line.strip()
            ]
            image_probe = subprocess.run(
                ["docker", "image", "inspect", docker_image, "--format", "{{.Size}}"],
                check=False,
                capture_output=True,
                text=True,
            )
            if image_probe.returncode == 0 and image_probe.stdout.strip().isdigit():
                evidence["docker_image_size_bytes"] = int(image_probe.stdout.strip())
            stats_probe = subprocess.run(
                [
                    "docker",
                    "stats",
                    "--no-stream",
                    "--format",
                    "{{.MemUsage}}|{{.CPUPerc}}",
                    container_name,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if stats_probe.returncode == 0:
                evidence["container_resource_sample"] = stats_probe.stdout.strip()

        with tempfile.TemporaryDirectory(prefix="issue861-docker-") as temp_dir:
            local_workspace = Path(temp_dir).resolve() / "workspace"
            local_workspace.mkdir()
            (local_workspace / "input.txt").write_text(
                "input-canary",
                encoding="utf-8",
            )
            remote_workspace = "/tmp/issue861"
            await deployment.runtime.upload(
                UploadRequest(
                    source_path=str(local_workspace),
                    target_path=remote_workspace,
                )
            )

            response = await deployment.runtime.execute(
                Command(
                    command=["/usr/local/bin/python3", "-c", _command_payload()],
                    cwd=remote_workspace,
                    env={"ISSUE861_SECRET": SYNTHETIC_SECRET},
                )
            )
            evidence["execute"] = {
                "exit_code": response.exit_code,
                "stdout_has_marker": "stdout-ok" in response.stdout,
                "stderr_has_marker": "stderr-ok" in response.stderr,
                "synthetic_env_visible": SYNTHETIC_SECRET in response.stdout,
            }

            followup = await deployment.runtime.execute(
                Command(
                    command=["python", "-c", _environment_probe_payload()],
                    cwd=remote_workspace,
                    check=False,
                )
            )
            evidence["synthetic_env_persisted_after_command"] = SYNTHETIC_SECRET in followup.stdout

            artifact_path = f"{remote_workspace}/out.txt"
            artifact_response = await deployment.runtime.execute(
                Command(
                    command=[
                        "python",
                        "-c",
                        ("from pathlib import Path; Path('out.txt').write_text('artifact-canary')"),
                    ],
                    cwd=remote_workspace,
                )
            )
            read_back = await deployment.runtime.read_file(
                ReadFileRequest(path=artifact_path, encoding="utf-8")
            )
            evidence["artifact_round_trip"] = bool(
                artifact_response.exit_code == 0 and read_back.content == "artifact-canary"
            )

            try:
                outside_read = await deployment.runtime.read_file(
                    ReadFileRequest(path="/etc/hostname", encoding="utf-8")
                )
            except Exception:
                outside_provider_workspace_read = False
            else:
                outside_provider_workspace_read = bool(outside_read.content)
            evidence["outside_provider_workspace_read_succeeded"] = outside_provider_workspace_read

            child_marker = f"{remote_workspace}/child-after-timeout.txt"
            try:
                await deployment.runtime.execute(
                    Command(
                        command=[
                            "/usr/local/bin/python3",
                            "-c",
                            _timeout_child_payload(),
                            child_marker,
                        ],
                        cwd=remote_workspace,
                        timeout=0.05,
                    )
                )
            except Exception as exc:
                evidence["timeout_child_cleanup"] = {
                    "parent_timeout_exception": type(exc).__name__,
                }
            else:
                evidence["timeout_child_cleanup"] = {
                    "parent_timeout_exception": None,
                }
            await asyncio.sleep(0.4)
            try:
                child_read = await deployment.runtime.read_file(
                    ReadFileRequest(path=child_marker, encoding="utf-8")
                )
            except Exception:
                child_survived = False
            else:
                child_survived = child_read.content == "survived"
            evidence["timeout_child_cleanup"]["child_survived_parent_timeout"] = child_survived

            egress = await deployment.runtime.execute(
                Command(
                    command=["python", "-c", _egress_payload()],
                    timeout=10,
                    check=False,
                )
            )
            evidence["egress"] = {
                "exit_code": egress.exit_code,
                "stdout": egress.stdout.strip()[:200],
                "stderr": egress.stderr.strip()[:500],
            }
            if internal_network:
                evidence["internal_network_blocked_egress"] = egress.exit_code not in (
                    0,
                    None,
                )
    finally:
        stopped = monotonic()
        await deployment.stop()
        evidence["stop_seconds"] = monotonic() - stopped

    evidence["passed_core_semantics"] = bool(
        evidence["swerex_version"] == EXPECTED_VERSION
        and evidence["docker_image_explicitly_pinned"]
        and evidence["execute"]["exit_code"] == 0
        and evidence["execute"]["stdout_has_marker"]
        and evidence["execute"]["stderr_has_marker"]
        and evidence["artifact_round_trip"]
    )
    if internal_network:
        evidence["passed_core_semantics"] = bool(
            evidence["passed_core_semantics"] and evidence["internal_network_blocked_egress"]
        )
    return evidence


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backend",
        choices=("local", "docker", "docker-internal"),
        required=True,
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        if args.backend == "local":
            evidence = await _run_local()
        else:
            evidence = await _run_docker(backend=args.backend)
    except Exception as exc:
        evidence = {
            "backend": args.backend,
            "expected_version": EXPECTED_VERSION,
            "pinned_revision": PINNED_REVISION,
            "host_platform": platform.platform(),
            "python": sys.version,
            "passed_core_semantics": False,
            "fatal_error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc()[-4000:],
            },
        }

    rendered = json.dumps(evidence, indent=2, sort_keys=True)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if evidence.get("passed_core_semantics") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
