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


def _timeout_payload() -> str:
    return "import time; time.sleep(2); print('unexpected-timeout-completion')"


def _egress_payload() -> str:
    return (
        "import urllib.request; "
        "r=urllib.request.urlopen('http://example.com', timeout=5); "
        "print(r.status)"
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


async def _run_docker(*, network_none: bool) -> dict[str, Any]:
    import swerex
    from swerex.deployment.docker import DockerDeployment
    from swerex.runtime.abstract import Command, ReadFileRequest, UploadRequest

    backend = "docker-network-none" if network_none else "docker"
    docker_args = ["--network=none"] if network_none else []
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
                    command=["python", "-c", _command_payload()],
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
            if network_none:
                evidence["network_none_blocked_egress"] = egress.exit_code not in (
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
    if network_none:
        evidence["passed_core_semantics"] = bool(
            evidence["passed_core_semantics"] and evidence["network_none_blocked_egress"]
        )
    return evidence


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backend",
        choices=("local", "docker", "docker-network-none"),
        required=True,
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        if args.backend == "local":
            evidence = await _run_local()
        else:
            evidence = await _run_docker(network_none=args.backend == "docker-network-none")
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
