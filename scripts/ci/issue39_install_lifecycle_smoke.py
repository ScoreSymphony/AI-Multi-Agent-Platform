from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

BOOTSTRAP_PASSWORD = "issue39-ci-only-password"
READY_TIMEOUT_SECONDS = 20.0


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(
    executable: str,
    *args: str,
    env: dict[str, str],
    stdin: str | None = None,
) -> str:
    try:
        completed = subprocess.run(
            [executable, *args],
            check=True,
            env=env,
            input=stdin,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        command = " ".join((executable, *args))
        raise RuntimeError(
            f"command failed with exit code {exc.returncode}: {command}\n"
            f"stdout:\n{exc.stdout or ''}\n"
            f"stderr:\n{exc.stderr or ''}"
        ) from exc
    return completed.stdout.strip()


def _wait_until_ready(port: int, process: subprocess.Popen[str], log_path: Path) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    url = f"http://127.0.0.1:{port}/api/v1/readiness"

    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(
                f"platform-server exited before readiness with code {return_code}:\n"
                f"{log_path.read_text(encoding='utf-8')}"
            )

        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                payload = json.load(response)
            if response.status == 200 and payload.get("ready") is True:
                return
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
            pass

        time.sleep(0.2)

    raise RuntimeError(
        f"platform-server did not become ready within {READY_TIMEOUT_SECONDS:.0f}s:\n"
        f"{log_path.read_text(encoding='utf-8')}"
    )


def _start_and_stop_once(
    executable: str,
    *,
    env: dict[str, str],
    port: int,
    log_path: Path,
) -> None:
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [executable, "serve"],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_until_ready(port, process, log_path)
            process.send_signal(signal.SIGINT)
            process.wait(timeout=10)
            if process.returncode != 0:
                raise RuntimeError(
                    f"platform-server did not shut down cleanly: {process.returncode}\n"
                    f"{log_path.read_text(encoding='utf-8')}"
                )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def main() -> int:
    executable = shutil.which("platform-server")
    if executable is None:
        raise RuntimeError("platform-server console entrypoint is not installed")

    with tempfile.TemporaryDirectory(prefix="issue39-install-smoke-") as temporary_directory:
        root = Path(temporary_directory)
        data_dir = root / "data"
        port = _reserve_loopback_port()

        env = os.environ.copy()
        env.update(
            {
                "AI_MAP_DATA_DIR": str(data_dir),
                "AI_MAP_HOST": "127.0.0.1",
                "AI_MAP_PORT": str(port),
                "AI_MAP_SECURE_COOKIE": "false",
                "AI_MAP_LOG_LEVEL": "warning",
            }
        )

        _run(
            executable,
            "bootstrap-admin",
            "--username",
            "ci-admin",
            "--password-stdin",
            env=env,
            stdin=f"{BOOTSTRAP_PASSWORD}\n",
        )
        first_smoke = _run(executable, "smoke", env=env)

        _start_and_stop_once(
            executable,
            env=env,
            port=port,
            log_path=root / "first-server.log",
        )
        _start_and_stop_once(
            executable,
            env=env,
            port=port,
            log_path=root / "second-server.log",
        )

        second_smoke = _run(executable, "smoke", env=env)
        if second_smoke != first_smoke:
            raise RuntimeError(
                "retry-safe canonical smoke changed across real process restart:\n"
                f"before: {first_smoke}\n"
                f"after:  {second_smoke}"
            )

    print("issue #39 fresh-install lifecycle smoke succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
