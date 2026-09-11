from __future__ import annotations

import asyncio
import base64
import json
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
from typing import Any

import pytest

PIPELOCK_TEST_BIN = os.getenv("PIPELOCK_730_BIN")
FIXTURE_DIR = Path(__file__).parent / "fixtures"
CORPUS_PATH = FIXTURE_DIR / "pipelock_adversarial_cases.json"
WEBSOCKET_CONFIG = FIXTURE_DIR / "pipelock_websocket_audit.yaml"
WEBSOCKET_FIXTURE = FIXTURE_DIR / "websocket_echo_server.py"

REQUIRED_CATEGORIES = {
    "tool_poisoning",
    "descriptor_drift",
    "response_injection",
    "secret_dlp",
    "encoding_variant",
    "multistage_exfiltration",
    "ssrf",
    "dns_rebinding",
    "ipv6_private",
    "websocket_dlp",
}


def _corpus() -> list[dict[str, Any]]:
    data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    return data


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_tcp(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"fixture exited before listening on 127.0.0.1:{port}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"fixture did not listen on 127.0.0.1:{port}")


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
def _running_fixture(path: Path) -> Iterator[int]:
    port = _free_loopback_port()
    process = subprocess.Popen(
        [sys.executable, str(path), "--port", str(port)],
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
def _pipelock(tmp_path: Path, *, log_name: str) -> Iterator[tuple[int, Path]]:
    assert PIPELOCK_TEST_BIN is not None
    port = _free_loopback_port()
    home = tmp_path / f"home-{log_name}"
    home.mkdir()
    log_path = tmp_path / f"{log_name}.log"
    log_handle = log_path.open("w", encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(home)
    process = subprocess.Popen(
        (
            PIPELOCK_TEST_BIN,
            "run",
            "--config",
            str(WEBSOCKET_CONFIG),
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
        yield port, log_path
    finally:
        _stop(process)
        log_handle.close()


def _fetch(proxy_port: int, target: str) -> None:
    query = urllib.parse.urlencode({"url": target})
    request_url = f"http://127.0.0.1:{proxy_port}/fetch?{query}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request_url, timeout=10) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        exc.read()


def _transform(case: dict[str, Any]) -> str:
    payload = str(case["payload"])
    if case["transform"] == "plain":
        return payload
    if case["transform"] == "base64":
        return base64.b64encode(payload.encode("utf-8")).decode("ascii")
    raise AssertionError(f"unsupported live transform: {case['transform']}")


def test_adversarial_corpus_tracks_every_required_issue_730_attack_class() -> None:
    cases = _corpus()

    assert {case["category"] for case in cases} == REQUIRED_CATEGORIES
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(case["status"] in {"live", "pending", "covered_elsewhere"} for case in cases)
    assert all(case["notes"] for case in cases)


def test_live_secret_fixtures_are_explicitly_synthetic() -> None:
    live_secret_cases = [
        case for case in _corpus() if case["status"] == "live" and "dlp" in case["category"]
    ]

    assert live_secret_cases
    for case in live_secret_cases:
        assert "EVALUATIONONLY" in case["payload"]
        assert "Synthetic" in case["notes"]


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
@pytest.mark.parametrize("case_id", ["secret-dlp-plain-query", "secret-dlp-base64-path"])
def test_pipelock_detects_synthetic_secret_in_http_url_variants(
    case_id: str,
    tmp_path: Path,
) -> None:
    case = next(item for item in _corpus() if item["id"] == case_id)
    transformed = _transform(case)
    if case["transform"] == "plain":
        target = "https://example.com/?token=" + urllib.parse.quote(transformed, safe="")
    else:
        target = "https://example.com/collect/" + urllib.parse.quote(transformed, safe="")

    with _pipelock(tmp_path, log_name=case_id) as (proxy_port, log_path):
        _fetch(proxy_port, target)

    log_text = log_path.read_text(encoding="utf-8")
    lowered = log_text.lower()
    assert str(case["expected_signal"]).lower() in lowered
    assert str(case["payload"]) not in log_text
    assert transformed not in log_text


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_pipelock_detects_synthetic_secret_in_websocket_text_frame(tmp_path: Path) -> None:
    websockets = pytest.importorskip("websockets")
    case = next(item for item in _corpus() if item["id"] == "secret-dlp-websocket-frame")
    payload = _transform(case)

    with _running_fixture(WEBSOCKET_FIXTURE) as echo_port:
        with _pipelock(tmp_path, log_name=str(case["id"])) as (proxy_port, log_path):

            async def scenario() -> None:
                target = f"ws://127.0.0.1:{echo_port}/echo"
                query = urllib.parse.urlencode({"url": target})
                proxy_url = f"ws://127.0.0.1:{proxy_port}/ws?{query}"
                try:
                    async with websockets.connect(
                        proxy_url,
                        compression=None,
                        open_timeout=5,
                        close_timeout=2,
                    ) as websocket:
                        await websocket.send(payload)
                        await asyncio.sleep(0.25)
                except websockets.exceptions.ConnectionClosed:
                    pass

            asyncio.run(scenario())

    log_text = log_path.read_text(encoding="utf-8")
    assert str(case["expected_signal"]).lower() in log_text.lower()
    assert payload not in log_text
