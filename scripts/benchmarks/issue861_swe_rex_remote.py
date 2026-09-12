#!/usr/bin/env python3
"""Exercise a pinned SWE-ReX server through RemoteDeployment on loopback."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from time import monotonic
from typing import Any

PINNED_REVISION = "5c995c365dfb1fd5bc56fda688be5d8538f9931f"
EXPECTED_VERSION = "1.4.0"
AUTH_TOKEN = "issue861-loopback-auth-token"
WRONG_AUTH_TOKEN = "issue861-wrong-auth-token"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_until_alive(deployment: Any, process: subprocess.Popen[str]) -> float:
    started = monotonic()
    last_error: Exception | None = None
    for _ in range(100):
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise RuntimeError(
                f"SWE-ReX server exited early with {process.returncode}: {stderr[-2000:]}"
            )
        try:
            if bool(await deployment.is_alive()):
                return monotonic() - started
        except Exception as exc:
            last_error = exc
        await asyncio.sleep(0.1)
    timeout = TimeoutError("SWE-ReX loopback server did not become healthy")
    if last_error is not None:
        raise timeout from last_error
    raise timeout


async def _run() -> dict[str, Any]:
    import swerex
    from swerex.deployment.remote import RemoteDeployment
    from swerex.runtime.abstract import Command, ReadFileRequest, WriteFileRequest

    executable = shutil.which("swerex-remote")
    if executable is None:
        raise RuntimeError("swerex-remote executable was not found after pinned installation")

    port = _free_port()
    server = subprocess.Popen(
        [
            executable,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--auth-token",
            AUTH_TOKEN,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deployment = RemoteDeployment(
        auth_token=AUTH_TOKEN,
        host="http://127.0.0.1",
        port=port,
        timeout=1.0,
    )
    await deployment.start()

    evidence: dict[str, Any] = {
        "backend": "remote-loopback",
        "host_platform": platform.platform(),
        "python": sys.version,
        "swerex_version": swerex.__version__,
        "expected_version": EXPECTED_VERSION,
        "pinned_revision": PINNED_REVISION,
        "transport_scheme": "http",
        "bind_host": "127.0.0.1",
        "auth_token_recorded": False,
    }

    try:
        evidence["startup_seconds"] = await _wait_until_alive(deployment, server)
        evidence["auth_success"] = bool(await deployment.is_alive())

        wrong = RemoteDeployment(
            auth_token=WRONG_AUTH_TOKEN,
            host="http://127.0.0.1",
            port=port,
            timeout=1.0,
        )
        await wrong.start()
        try:
            try:
                wrong_health = await wrong.is_alive()
            except Exception as exc:
                evidence["wrong_auth_rejected"] = True
                evidence["wrong_auth_error_type"] = type(exc).__name__
            else:
                evidence["wrong_auth_rejected"] = not bool(wrong_health)
                evidence["wrong_auth_error_type"] = None
        finally:
            try:
                await wrong.stop()
            except Exception as exc:
                evidence["wrong_auth_close_rejected"] = True
                evidence["wrong_auth_close_error_type"] = type(exc).__name__
            else:
                evidence["wrong_auth_close_rejected"] = False

        with tempfile.TemporaryDirectory(prefix="issue861-remote-") as temp_dir:
            root = Path(temp_dir).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside.txt"
            outside.write_text("remote-outside-canary", encoding="utf-8")

            command = await deployment.runtime.execute(
                Command(
                    command=[
                        sys.executable,
                        "-c",
                        (
                            "import sys; print('remote-stdout'); "
                            "print('remote-stderr', file=sys.stderr)"
                        ),
                    ],
                    cwd=str(workspace),
                    check=False,
                )
            )
            evidence["execute"] = {
                "exit_code": command.exit_code,
                "stdout_marker": "remote-stdout" in command.stdout,
                "stderr_marker": "remote-stderr" in command.stderr,
            }

            provider_file = workspace / "provider.txt"
            await deployment.runtime.write_file(
                WriteFileRequest(path=str(provider_file), content="remote-file-canary")
            )
            read_back = await deployment.runtime.read_file(
                ReadFileRequest(path=str(provider_file), encoding="utf-8")
            )
            evidence["file_round_trip"] = read_back.content == "remote-file-canary"

            outside_read = await deployment.runtime.read_file(
                ReadFileRequest(path=str(outside), encoding="utf-8")
            )
            evidence["outside_workspace_read_succeeded"] = (
                outside_read.content == "remote-outside-canary"
            )

            concurrent = await asyncio.gather(
                *(
                    deployment.runtime.execute(
                        Command(
                            command=[
                                sys.executable,
                                "-c",
                                f"print('concurrent-{index}')",
                            ],
                            cwd=str(workspace),
                            check=False,
                        )
                    )
                    for index in range(4)
                )
            )
            evidence["concurrent_commands"] = {
                "count": len(concurrent),
                "all_succeeded": all(item.exit_code == 0 for item in concurrent),
                "stdout_markers": [item.stdout.strip() for item in concurrent],
            }
    finally:
        await deployment.stop()
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        evidence["server_process_cleaned_up"] = server.poll() is not None

    evidence["passed_core_semantics"] = bool(
        evidence["swerex_version"] == EXPECTED_VERSION
        and evidence["auth_success"]
        and evidence.get("wrong_auth_rejected") is True
        and evidence["execute"]["exit_code"] == 0
        and evidence["execute"]["stdout_marker"]
        and evidence["execute"]["stderr_marker"]
        and evidence["file_round_trip"]
        and evidence["outside_workspace_read_succeeded"]
        and evidence["concurrent_commands"]["all_succeeded"]
        and evidence["server_process_cleaned_up"]
    )
    return evidence


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        evidence = await _run()
    except Exception as exc:
        evidence = {
            "backend": "remote-loopback",
            "host_platform": platform.platform(),
            "python": sys.version,
            "expected_version": EXPECTED_VERSION,
            "pinned_revision": PINNED_REVISION,
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