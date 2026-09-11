from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

PIPELOCK_TEST_BIN = os.getenv("PIPELOCK_730_BIN")
FIXTURE_DIR = Path(__file__).parent / "fixtures"
PIPELOCK_CONFIG = FIXTURE_DIR / "pipelock_outage_enforced.yaml"
TARGET_FIXTURE = FIXTURE_DIR / "pipelock_outage_target_server.py"
TARGET_SENTINEL = "issue-730-outage-target-ok"


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_tcp(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"process exited before listening on 127.0.0.1:{port}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"process did not listen on 127.0.0.1:{port}")


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@contextmanager
def _target_server(tmp_path: Path) -> Iterator[tuple[int, Path]]:
    port = _free_loopback_port()
    count_file = tmp_path / "target-count.txt"
    process = subprocess.Popen(
        [
            sys.executable,
            str(TARGET_FIXTURE),
            "--port",
            str(port),
            "--count-file",
            str(count_file),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        _wait_for_tcp(port, process)
        yield port, count_file
    finally:
        _stop(process)


def _start_pipelock(tmp_path: Path, *, port: int, run_name: str) -> tuple[subprocess.Popen[str], Path]:
    assert PIPELOCK_TEST_BIN is not None
    log_path = tmp_path / f"{run_name}.log"
    log_handle = log_path.open("w", encoding="utf-8")
    home = tmp_path / f"home-{run_name}"
    home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(home)
    process = subprocess.Popen(
        (
            PIPELOCK_TEST_BIN,
            "run",
            "--config",
            str(PIPELOCK_CONFIG),
            "--listen",
            f"127.0.0.1:{port}",
        ),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    process._pipelock_log_handle = log_handle  # type: ignore[attr-defined]
    _wait_for_tcp(port, process)
    return process, log_path


def _stop_pipelock(process: subprocess.Popen[str]) -> None:
    _stop(process)
    log_handle = getattr(process, "_pipelock_log_handle", None)
    if log_handle is not None:
        log_handle.close()


def _fetch(proxy_port: int, target: str) -> str:
    query = urllib.parse.urlencode({"url": target})
    request_url = f"http://127.0.0.1:{proxy_port}/fetch?{query}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request_url, timeout=5) as response:
        return response.read().decode("utf-8", errors="replace")


def _target_count(path: Path) -> int:
    return int(path.read_text(encoding="utf-8"))


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_live_enforced_mediation_fails_closed_during_runtime_outage_and_recovers(
    tmp_path: Path,
) -> None:
    proxy_port = _free_loopback_port()

    with _target_server(tmp_path) as (target_port, count_file):
        target = f"http://127.0.0.1:{target_port}/ok"

        first_process, first_log = _start_pipelock(tmp_path, port=proxy_port, run_name="before-outage")
        try:
            first_body = _fetch(proxy_port, target)
        finally:
            _stop_pipelock(first_process)

        assert TARGET_SENTINEL in first_body
        assert _target_count(count_file) == 1

        with pytest.raises(urllib.error.URLError):
            _fetch(proxy_port, target)
        assert _target_count(count_file) == 1

        second_process, second_log = _start_pipelock(
            tmp_path,
            port=proxy_port,
            run_name="after-recovery",
        )
        try:
            recovered_body = _fetch(proxy_port, target)
        finally:
            _stop_pipelock(second_process)

        assert TARGET_SENTINEL in recovered_body
        assert _target_count(count_file) == 2

    assert "listening" in first_log.read_text(encoding="utf-8").lower()
    assert "listening" in second_log.read_text(encoding="utf-8").lower()
