from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

import pytest

from ai_multi_agent_platform.adapters.hermes import UrllibHermesHttpTransport

PIPELOCK_TEST_BIN = os.getenv("PIPELOCK_730_BIN")
FIXTURE_DIR = Path(__file__).parents[2] / "fixtures"
PIPELOCK_CONFIG = FIXTURE_DIR / "pipelock_websocket_audit.yaml"
HTTP_TARGET = FIXTURE_DIR / "pipelock_profile_bypass_target.py"
WEBSOCKET_TARGET = FIXTURE_DIR / "websocket_adversarial_server.py"


def _free_port() -> int:
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
def _http_target(tmp_path: Path) -> Iterator[tuple[int, Path]]:
    port = _free_port()
    marker = tmp_path / "profile-http-target.jsonl"
    process = subprocess.Popen(
        (
            sys.executable,
            str(HTTP_TARGET),
            "--port",
            str(port),
            "--marker",
            str(marker),
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        _wait_for_tcp(port, process)
        yield port, marker
    finally:
        _stop(process)


@contextmanager
def _websocket_target(tmp_path: Path) -> Iterator[tuple[int, Path]]:
    port = _free_port()
    marker = tmp_path / "profile-websocket-target.txt"
    process = subprocess.Popen(
        (
            sys.executable,
            str(WEBSOCKET_TARGET),
            "--port",
            str(port),
            "--marker",
            str(marker),
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        _wait_for_tcp(port, process)
        yield port, marker
    finally:
        _stop(process)


@contextmanager
def _running_pipelock(tmp_path: Path) -> Iterator[Path]:
    assert PIPELOCK_TEST_BIN is not None
    port = _free_port()
    home = tmp_path / "pipelock-home"
    home.mkdir()
    log_path = tmp_path / "pipelock-profile-bypass.log"
    log_handle: TextIO = log_path.open("w", encoding="utf-8")
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
    try:
        _wait_for_tcp(port, process)
        yield log_path
    finally:
        _stop(process)
        log_handle.close()


def _records(marker: Path) -> list[dict[str, str]]:
    if not marker.exists():
        return []
    return [json.loads(line) for line in marker.read_text(encoding="utf-8").splitlines()]


def _direct_get(url: str) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        return response.read()


def _direct_post(url: str, payload: bytes, content_type: str) -> bytes:
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": content_type},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        return response.read()


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_in_process_http_mcp_http_and_redirect_paths_bypass_running_pipelock(
    tmp_path: Path,
) -> None:
    with _http_target(tmp_path) as (target_port, marker):
        base = f"http://127.0.0.1:{target_port}"
        with _running_pipelock(tmp_path) as pipelock_log:
            ordinary = _direct_get(f"{base}/issue-730-direct-http")
            mcp_http = _direct_post(
                f"{base}/mcp",
                b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
                "application/json",
            )
            redirected = _direct_get(f"{base}/issue-730-redirect")

        assert b'"ok":true' in ordinary
        assert b'"ok":true' in mcp_http
        assert b'"path":"/issue-730-redirect-target"' in redirected

    records = _records(marker)
    paths = [record["path"] for record in records]
    assert "/issue-730-direct-http" in paths
    assert "/mcp" in paths
    assert "/issue-730-redirect" in paths
    assert "/issue-730-redirect-target" in paths
    log_text = pipelock_log.read_text(encoding="utf-8")
    for marker_text in (
        "issue-730-direct-http",
        "issue-730-redirect",
        "issue-730-redirect-target",
        "tools/list",
    ):
        assert marker_text not in log_text


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_direct_websocket_path_bypasses_running_pipelock(tmp_path: Path) -> None:
    websockets = pytest.importorskip("websockets")
    with _websocket_target(tmp_path) as (target_port, marker):
        with _running_pipelock(tmp_path) as pipelock_log:

            async def scenario() -> None:
                url = f"ws://127.0.0.1:{target_port}/issue-730-direct-websocket"
                async with websockets.connect(
                    url,
                    compression=None,
                    open_timeout=5,
                    close_timeout=2,
                ) as websocket:
                    await websocket.send("issue-730-direct-websocket-payload")
                    assert await asyncio.wait_for(websocket.recv(), timeout=5) == "ack"

            asyncio.run(scenario())

    lines = marker.read_text(encoding="utf-8").splitlines()
    assert "recv:issue-730-direct-websocket-payload" in lines
    log_text = pipelock_log.read_text(encoding="utf-8")
    assert "issue-730-direct-websocket-payload" not in log_text


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_current_hermes_http_transport_bypasses_running_pipelock(tmp_path: Path) -> None:
    with _http_target(tmp_path) as (target_port, marker):
        target = f"http://127.0.0.1:{target_port}/v1/runs"
        with _running_pipelock(tmp_path) as pipelock_log:
            response = asyncio.run(
                UrllibHermesHttpTransport().request_json(
                    "POST",
                    target,
                    payload={"prompt": "issue-730-hermes-direct-path"},
                    headers={},
                    timeout_seconds=5,
                )
            )

        assert response.status_code == 200
        assert response.payload == {"ok": True, "path": "/v1/runs"}

    records = _records(marker)
    hermes_record = next(record for record in records if record["path"] == "/v1/runs")
    assert "issue-730-hermes-direct-path" in hermes_record["body"]
    assert "issue-730-hermes-direct-path" not in pipelock_log.read_text(encoding="utf-8")
