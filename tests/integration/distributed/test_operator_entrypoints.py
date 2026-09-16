from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from ai_multi_agent_platform.cli.credentials import CredentialStore

_PASSWORD = "operator-entrypoint-password"
_TRANSPORT_KEY = "operator-entrypoint-hmac"
_TIMEOUT_SECONDS = 20.0
_PROFILE = Path("deploy/distributed/profiles/multi-local-workers.json")


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise AssertionError(f"required shipped console entrypoint is not installed: {name}")
    return executable


def _run(
    executable: str,
    *args: str,
    env: dict[str, str],
    stdin: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [executable, *args],
        env=env,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=_TIMEOUT_SECONDS,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"command failed ({completed.returncode}): {executable} {' '.join(args)}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    return completed


def _wait_for_readiness(port: int, process: subprocess.Popen[str], log_path: Path) -> None:
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    url = f"http://127.0.0.1:{port}/api/v1/readiness"
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise AssertionError(
                f"distributed server exited before readiness ({return_code}):\n"
                f"{log_path.read_text(encoding='utf-8')}"
            )
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                payload = json.load(response)
            if response.status == 200 and payload.get("ready") is True:
                return
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(0.1)
    raise AssertionError(
        f"distributed server did not become ready:\n{log_path.read_text(encoding='utf-8')}"
    )


def _wait_for_worker(
    *,
    endpoint: str,
    session_cookie: str,
    worker_id: str,
    worker_process: subprocess.Popen[str],
    worker_log: Path,
) -> dict[str, object]:
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    url = f"{endpoint}/api/v1/workers/{worker_id}"
    while time.monotonic() < deadline:
        return_code = worker_process.poll()
        if return_code is not None:
            raise AssertionError(
                f"platform-worker exited before healthy registration ({return_code}):\n"
                f"{worker_log.read_text(encoding='utf-8')}"
            )
        request = urllib.request.Request(url, headers={"Cookie": session_cookie})
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                payload = json.load(response)
            if (
                response.status == 200
                and isinstance(payload, dict)
                and payload.get("status") == "healthy"
            ):
                return payload
        except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
            pass
        time.sleep(0.1)
    raise AssertionError(
        f"platform-worker did not become healthy:\n{worker_log.read_text(encoding='utf-8')}"
    )


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def test_shipped_broker_server_cli_and_worker_entrypoints_register_worker(tmp_path: Path) -> None:
    broker_executable = _executable("platform-message-broker")
    server_executable = _executable("platform-distributed-server")
    worker_executable = _executable("platform-worker")
    platform_executable = _executable("platform")

    raw_profile = json.loads(_PROFILE.read_text(encoding="utf-8"))
    host_root = (tmp_path / "worker-workspaces").resolve()
    raw_profile["nodes"][0]["deployment"]["workspace_root"] = str(host_root)
    profile_path = tmp_path / "multi-local-workers.json"
    profile_path.write_text(json.dumps(raw_profile), encoding="utf-8")

    node = raw_profile["nodes"][0]
    reporter_id = str(node["reporter_worker_id"])
    host_ref = str(node["deployment"]["host_ref"])
    broker_port = _reserve_loopback_port()
    server_port = _reserve_loopback_port()
    endpoint = f"http://127.0.0.1:{server_port}"
    data_dir = tmp_path / "control-plane"
    cli_config = tmp_path / "cli.json"
    secret_file = tmp_path / "reporter.token"

    env = os.environ.copy()
    env.update(
        {
            "AI_MAP_DATA_DIR": str(data_dir),
            "AI_MAP_HOST": "127.0.0.1",
            "AI_MAP_PORT": str(server_port),
            "AI_MAP_SECURE_COOKIE": "false",
            "AI_MAP_LOG_LEVEL": "warning",
            "PLATFORM_MESSAGE_BROKER_HOST": "127.0.0.1",
            "PLATFORM_MESSAGE_BROKER_PORT": str(broker_port),
            "PLATFORM_TRANSPORT_AUTH_KEY": _TRANSPORT_KEY,
        }
    )

    broker_log = tmp_path / "broker.log"
    server_log = tmp_path / "server.log"
    worker_log = tmp_path / "worker.log"
    with (
        broker_log.open("w", encoding="utf-8") as broker_stream,
        server_log.open("w", encoding="utf-8") as server_stream,
        worker_log.open("w", encoding="utf-8") as worker_stream,
    ):
        broker = subprocess.Popen(
            [
                broker_executable,
                "--host",
                "127.0.0.1",
                "--port",
                str(broker_port),
            ],
            env=env,
            stdout=broker_stream,
            stderr=subprocess.STDOUT,
            text=True,
        )
        server: subprocess.Popen[str] | None = None
        worker: subprocess.Popen[str] | None = None
        try:
            _run(
                server_executable,
                "--profile",
                str(profile_path),
                "bootstrap-admin",
                "--username",
                "operator-admin",
                "--password-stdin",
                env=env,
                stdin=f"{_PASSWORD}\n",
            )

            server = subprocess.Popen(
                [server_executable, "--profile", str(profile_path), "serve"],
                env=env,
                stdout=server_stream,
                stderr=subprocess.STDOUT,
                text=True,
            )
            _wait_for_readiness(server_port, server, server_log)

            _run(
                platform_executable,
                "--config",
                str(cli_config),
                "--endpoint",
                endpoint,
                "auth",
                "login",
                "--username",
                "operator-admin",
                "--password-stdin",
                env=env,
                stdin=f"{_PASSWORD}\n",
            )
            credential_store = CredentialStore.load(cli_config)
            assert len(credential_store.profiles) == 1
            credential_state = next(iter(credential_store.profiles.values()))
            assert credential_state.mode == "session"
            assert credential_state.session_cookie is not None
            provisioned = _run(
                platform_executable,
                "--config",
                str(cli_config),
                "--endpoint",
                endpoint,
                "--yes",
                "worker",
                "provision",
                reporter_id,
                "--secret-file",
                str(secret_file),
                env=env,
            )
            assert "secret_file_written" in provisioned.stdout
            worker_secret = secret_file.read_text(encoding="utf-8").strip()
            assert worker_secret

            worker_env = dict(env)
            worker_env["PLATFORM_WORKER_TOKEN"] = worker_secret
            worker = subprocess.Popen(
                [
                    worker_executable,
                    "--profile",
                    str(profile_path),
                    "--host-ref",
                    host_ref,
                    "--worker-id",
                    reporter_id,
                    "--control-plane-url",
                    endpoint,
                    "--broker-host",
                    "127.0.0.1",
                    "--broker-port",
                    str(broker_port),
                    "--heartbeat-seconds",
                    "0.1",
                ],
                env=worker_env,
                stdout=worker_stream,
                stderr=subprocess.STDOUT,
                text=True,
            )

            _wait_for_worker(
                endpoint=endpoint,
                session_cookie=credential_state.session_cookie,
                worker_id=reporter_id,
                worker_process=worker,
                worker_log=worker_log,
            )
            shown = _run(
                platform_executable,
                "--config",
                str(cli_config),
                "--endpoint",
                endpoint,
                "--json",
                "worker",
                "show",
                reporter_id,
                env=env,
            )
            shown_payload = json.loads(shown.stdout)
            assert isinstance(shown_payload, dict)
            shown_data = shown_payload.get("data")
            assert isinstance(shown_data, dict)
            assert shown_data.get("id") == reporter_id
            assert shown_data.get("status") == "healthy"
            assert (host_root / reporter_id).is_dir()
        finally:
            if worker is not None:
                _terminate(worker)
            if server is not None:
                _terminate(server)
            _terminate(broker)
