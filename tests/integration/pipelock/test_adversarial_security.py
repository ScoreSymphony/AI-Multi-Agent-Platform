from __future__ import annotations

import asyncio
import base64
import os
import socket
import subprocess
import sys
import time
import urllib.parse
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

PIPELOCK_TEST_BIN = os.getenv("PIPELOCK_730_BIN")
_RUNNER_TEMP = os.getenv("RUNNER_TEMP")
PIPELOCK_TEST_CONFIG = os.getenv("PIPELOCK_730_CONFIG") or (
    str(Path(_RUNNER_TEMP) / "pipelock-audit.yaml") if _RUNNER_TEMP else None
)
FIXTURE_DIR = Path(__file__).parents[2] / "fixtures"
WEBSOCKET_CONFIG = FIXTURE_DIR / "pipelock_websocket_audit.yaml"
ADVERSARIAL_SERVER = FIXTURE_DIR / "websocket_adversarial_server.py"
SYNTHETIC_AWS_ACCESS_ID = "AKIA" + "IOSFODNN7EXAMPLE"


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
def _adversarial_server(marker: Path) -> Iterator[int]:
    port = _free_loopback_port()
    process = subprocess.Popen(
        (
            sys.executable,
            str(ADVERSARIAL_SERVER),
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
        yield port
    finally:
        _stop(process)


@contextmanager
def _pipelock(tmp_path: Path, *, name: str) -> Iterator[tuple[int, Path]]:
    assert PIPELOCK_TEST_BIN is not None
    port = _free_loopback_port()
    home = tmp_path / f"home-{name}"
    home.mkdir()
    log_path = tmp_path / f"{name}.log"
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


@contextmanager
def _pipelock_from_config(
    *,
    config: Path,
    home: Path,
    tmp_path: Path,
    name: str,
) -> Iterator[tuple[int, Path]]:
    assert PIPELOCK_TEST_BIN is not None
    port = _free_loopback_port()
    log_path = tmp_path / f"{name}.log"
    log_handle = log_path.open("w", encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(home)
    process = subprocess.Popen(
        (
            PIPELOCK_TEST_BIN,
            "run",
            "--config",
            str(config),
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


def _replace_nested_scalar(text: str, *, section: str, key: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    in_section = False
    changed = False
    for index, line in enumerate(lines):
        if line.startswith(f"{section}:"):
            in_section = True
            continue
        if in_section and line.strip() and not line.startswith((" ", "\t", "#")):
            break
        if not in_section:
            continue
        stripped = line.lstrip()
        if not stripped.startswith(f"{key}:"):
            continue
        indent = line[: len(line) - len(stripped)]
        lines[index] = f"{indent}{key}: {value}\n"
        changed = True
        break
    if not changed:
        raise AssertionError(f"missing {section}.{key} in generated Pipelock config")
    return "".join(lines)


def _strict_connect_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    assert PIPELOCK_TEST_BIN is not None
    assert PIPELOCK_TEST_CONFIG is not None
    home = tmp_path / "strict-connect-home"
    home.mkdir()
    config = tmp_path / "strict-connect.yaml"
    recorder = tmp_path / "strict-connect-recorder"
    pubkey = tmp_path / "strict-connect.pub"
    env = dict(os.environ)
    env["HOME"] = str(home)
    candidate_dir = Path(PIPELOCK_TEST_BIN).parent

    text = Path(PIPELOCK_TEST_CONFIG).read_text(encoding="utf-8")
    text = _replace_nested_scalar(
        text,
        section="forward_proxy",
        key="enabled",
        value="true",
    )
    text = _replace_nested_scalar(
        text,
        section="flight_recorder",
        key="require_receipts",
        value="true",
    )
    text = _replace_nested_scalar(
        text,
        section="flight_recorder",
        key="dir",
        value=f'"{recorder}"',
    )
    config.write_text(text, encoding="utf-8")

    completed = subprocess.run(
        (
            PIPELOCK_TEST_BIN,
            "signing",
            "pubkey",
            "--config",
            str(config),
            "--out",
            str(pubkey),
        ),
        capture_output=True,
        text=True,
        env=env,
        cwd=candidate_dir,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert pubkey.stat().st_size > 0
    return home, config, recorder, pubkey


def _wait_for_log_event(log_path: Path, event: str) -> str:
    needle = f'"event":"{event}"'
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        text = log_path.read_text(encoding="utf-8")
        if needle in text:
            return text
        time.sleep(0.05)
    raise AssertionError(f"Pipelock log did not record {event!r} before timeout")


def _verification_field(text: str, label: str) -> str:
    prefix = f"{label}:"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped.removeprefix(prefix).strip()
    raise AssertionError(f"receipt verifier did not report {label!r}")


def _marker_lines(marker: Path) -> list[str]:
    if not marker.exists():
        return []
    return marker.read_text(encoding="utf-8").splitlines()


def _proxy_url(proxy_port: int, upstream_port: int) -> str:
    target = f"ws://127.0.0.1:{upstream_port}/adversarial"
    query = urllib.parse.urlencode({"url": target})
    return f"ws://127.0.0.1:{proxy_port}/ws?{query}"


async def _expect_connection_blocked(websocket: object) -> None:
    from websockets.exceptions import ConnectionClosed

    with pytest.raises(ConnectionClosed):
        await asyncio.wait_for(websocket.recv(), timeout=5)  # type: ignore[attr-defined]


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_websocket_blocks_plain_secret_before_upstream(tmp_path: Path) -> None:
    websockets = pytest.importorskip("websockets")
    marker = tmp_path / "plain-secret-marker.txt"
    with _adversarial_server(marker) as upstream_port:
        with _pipelock(tmp_path, name="plain-secret") as (proxy_port, log_path):

            async def scenario() -> None:
                async with websockets.connect(
                    _proxy_url(proxy_port, upstream_port),
                    compression=None,
                    open_timeout=5,
                    close_timeout=2,
                ) as websocket:
                    await websocket.send(SYNTHETIC_AWS_ACCESS_ID)
                    await _expect_connection_blocked(websocket)

            asyncio.run(scenario())

        assert f"recv:{SYNTHETIC_AWS_ACCESS_ID}" not in _marker_lines(marker)
        log_text = log_path.read_text(encoding="utf-8").lower()
        assert "dlp" in log_text
        assert "aws" in log_text


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_websocket_blocks_base64_encoded_secret_before_upstream(tmp_path: Path) -> None:
    websockets = pytest.importorskip("websockets")
    encoded = base64.b64encode(SYNTHETIC_AWS_ACCESS_ID.encode()).decode()
    marker = tmp_path / "base64-secret-marker.txt"
    with _adversarial_server(marker) as upstream_port:
        with _pipelock(tmp_path, name="base64-secret") as (proxy_port, log_path):

            async def scenario() -> None:
                async with websockets.connect(
                    _proxy_url(proxy_port, upstream_port),
                    compression=None,
                    open_timeout=5,
                    close_timeout=2,
                ) as websocket:
                    await websocket.send(encoded)
                    await _expect_connection_blocked(websocket)

            asyncio.run(scenario())

        assert f"recv:{encoded}" not in _marker_lines(marker)
        log_text = log_path.read_text(encoding="utf-8").lower()
        assert "dlp" in log_text
        assert "aws" in log_text or "base64" in log_text


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_websocket_blocks_secret_split_across_messages(tmp_path: Path) -> None:
    websockets = pytest.importorskip("websockets")
    first = SYNTHETIC_AWS_ACCESS_ID[:8]
    second = SYNTHETIC_AWS_ACCESS_ID[8:]
    marker = tmp_path / "split-secret-marker.txt"
    with _adversarial_server(marker) as upstream_port:
        with _pipelock(tmp_path, name="split-secret") as (proxy_port, log_path):

            async def scenario() -> None:
                async with websockets.connect(
                    _proxy_url(proxy_port, upstream_port),
                    compression=None,
                    open_timeout=5,
                    close_timeout=2,
                ) as websocket:
                    await websocket.send(first)
                    assert await asyncio.wait_for(websocket.recv(), timeout=5) == "ack"
                    await websocket.send(second)
                    await _expect_connection_blocked(websocket)

            asyncio.run(scenario())

        lines = _marker_lines(marker)
        assert f"recv:{first}" in lines
        assert f"recv:{second}" not in lines
        log_text = log_path.read_text(encoding="utf-8").lower()
        assert "dlp" in log_text
        assert "cross" in log_text or "aws" in log_text


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_websocket_blocks_server_prompt_injection_before_client(tmp_path: Path) -> None:
    websockets = pytest.importorskip("websockets")
    marker = tmp_path / "response-injection-marker.txt"
    with _adversarial_server(marker) as upstream_port:
        with _pipelock(tmp_path, name="response-injection") as (proxy_port, log_path):

            async def scenario() -> None:
                async with websockets.connect(
                    _proxy_url(proxy_port, upstream_port),
                    compression=None,
                    open_timeout=5,
                    close_timeout=2,
                ) as websocket:
                    await websocket.send("trigger-injection")
                    await _expect_connection_blocked(websocket)

            asyncio.run(scenario())

        lines = _marker_lines(marker)
        assert "recv:trigger-injection" in lines
        assert "sent:injection" in lines
        log_text = log_path.read_text(encoding="utf-8").lower()
        assert "injection" in log_text
        assert "response" in log_text or "response_scan" in log_text


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None or PIPELOCK_TEST_CONFIG is None,
    reason="requires the pinned Pipelock #730 compatibility runtime and generated audit config",
)
def test_connect_require_receipts_uses_fresh_writer_and_verifies_chain(
    tmp_path: Path,
    record_property: Callable[[str, object], None],
) -> None:
    assert PIPELOCK_TEST_BIN is not None
    home, config, recorder, pubkey = _strict_connect_fixture(tmp_path)
    output = tmp_path / "strict-connect-output.txt"

    with _pipelock_from_config(
        config=config,
        home=home,
        tmp_path=tmp_path,
        name="strict-connect",
    ) as (proxy_port, log_path):
        completed = subprocess.run(
            (
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                "20",
                "--proxy",
                f"http://127.0.0.1:{proxy_port}",
                "--noproxy",
                "",
                "--output",
                str(output),
                "https://example.com/",
            ),
            capture_output=True,
            text=True,
            timeout=25,
        )
        assert completed.returncode == 0, completed.stderr
        assert output.stat().st_size > 0
        running_log = _wait_for_log_event(log_path, "tunnel_close")
        assert '"event":"tunnel_open"' in running_log
        assert "receipt_emission_failed" not in running_log
        assert "chain sealed: transcript root already emitted" not in running_log

    final_log = log_path.read_text(encoding="utf-8")
    assert "chain sealed: transcript root already emitted" not in final_log

    verification = subprocess.run(
        (
            PIPELOCK_TEST_BIN,
            "verify-receipt",
            "--chain",
            str(recorder),
            "--key",
            str(pubkey),
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert "CHAIN VALID" in verification.stdout
    record_property("strict_connect_chain_valid", "true")
    record_property("strict_connect_receipts", _verification_field(verification.stdout, "Receipts"))
    record_property("strict_connect_final_seq", _verification_field(verification.stdout, "Final seq"))
    record_property("strict_connect_root_hash", _verification_field(verification.stdout, "Root hash"))
    record_property("strict_connect_signer", _verification_field(verification.stdout, "Signer"))
    containment = _verification_field(verification.stdout, "Containment").split(" —", 1)[0]
    record_property("strict_connect_containment", containment)
    record_property("strict_connect_sealed_chain_error", "false")
