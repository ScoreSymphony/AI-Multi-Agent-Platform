from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

PIPELOCK_TEST_BIN = os.getenv("PIPELOCK_730_BIN")
FIXTURE_DIR = Path(__file__).parent / "fixtures"
PIPELOCK_CONFIG = FIXTURE_DIR / "pipelock_mcp_adversarial.yaml"
MCP_FIXTURE = FIXTURE_DIR / "mcp_adversarial_http_server.py"


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
def _upstream(mode: str) -> Iterator[int]:
    port = _free_loopback_port()
    process = subprocess.Popen(
        [sys.executable, str(MCP_FIXTURE), "--port", str(port), "--mode", mode],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        _wait_for_tcp(port, process)
        yield port
    finally:
        _stop(process)


@contextmanager
def _pipelock_listener(tmp_path: Path, upstream_port: int) -> Iterator[tuple[int, Path]]:
    assert PIPELOCK_TEST_BIN is not None
    port = _free_loopback_port()
    log_path = tmp_path / "pipelock-mcp-adversarial.log"
    log_handle = log_path.open("w", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(home)
    process = subprocess.Popen(
        (
            PIPELOCK_TEST_BIN,
            "mcp",
            "proxy",
            "--config",
            str(PIPELOCK_CONFIG),
            "--listen",
            f"127.0.0.1:{port}",
            "--upstream",
            f"http://127.0.0.1:{upstream_port}",
        ),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    try:
        _wait_for_tcp(port, process)
        yield port, log_path
    finally:
        _stop(process)
        log_handle.close()


def _post(port: int, request_id: int, method: str, params: dict[str, object]) -> tuple[int, str]:
    body = json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=10) as response:
            return int(response.status), response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_pipelock_blocks_poisoned_mcp_tool_description(tmp_path: Path) -> None:
    with _upstream("poison") as upstream_port:
        with _pipelock_listener(tmp_path, upstream_port) as (proxy_port, log_path):
            _status, body = _post(proxy_port, 1, "tools/list", {})

    assert '"error"' in body
    assert "~/.ssh/id_rsa" not in body
    log_text = log_path.read_text(encoding="utf-8")
    assert "tool poisoning detected in tools/list" in log_text
    assert '"message":"request blocked"' in log_text


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_pipelock_blocks_mcp_descriptor_drift_after_clean_baseline(tmp_path: Path) -> None:
    with _upstream("drift") as upstream_port:
        with _pipelock_listener(tmp_path, upstream_port) as (proxy_port, log_path):
            _first_status, first = _post(proxy_port, 1, "tools/list", {})
            _second_status, second = _post(proxy_port, 2, "tools/list", {})

    assert "lookup_invoice" in first
    assert '"error"' in second
    assert "sink.fixture.example" not in second
    assert "definition-drift" in log_path.read_text(encoding="utf-8")


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_pipelock_blocks_prompt_injection_in_mcp_tool_response(tmp_path: Path) -> None:
    with _upstream("response-injection") as upstream_port:
        with _pipelock_listener(tmp_path, upstream_port) as (proxy_port, log_path):
            _status, body = _post(
                proxy_port,
                1,
                "tools/call",
                {"name": "lookup_invoice", "arguments": {"invoice_number": "INV-730"}},
            )

    assert "Ignore all previous instructions" not in body
    assert '"error"' in body
    log_text = log_path.read_text(encoding="utf-8")
    assert "response_scan" in log_text or "prompt injection" in log_text.lower()
