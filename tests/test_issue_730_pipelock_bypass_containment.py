from __future__ import annotations

import asyncio
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

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig
from ai_multi_agent_platform.adapters.mcp_sdk import build_mcp_provider
from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistry,
    InvocationTrace,
)
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id

PIPELOCK_TEST_BIN = os.getenv("PIPELOCK_730_BIN")
PIPELOCK_TEST_CONFIG = os.getenv("PIPELOCK_730_CONFIG")
FIXTURE_DIR = Path(__file__).parent / "fixtures"
BYPASS_CONFIG = FIXTURE_DIR / "pipelock_bypass_audit.yaml"
HTTP_TARGET = FIXTURE_DIR / "pipelock_bypass_http_target.py"
MCP_DIRECT_EGRESS = FIXTURE_DIR / "mcp_stdio_direct_egress_server.py"
WEBSOCKET_TARGET = FIXTURE_DIR / "websocket_echo_server.py"
TARGET_SENTINEL = "issue-730-direct-egress-ok"


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
def _http_target(tmp_path: Path) -> Iterator[tuple[int, Path]]:
    port = _free_loopback_port()
    count_file = tmp_path / "direct-target-count.txt"
    process = subprocess.Popen(
        (
            sys.executable,
            str(HTTP_TARGET),
            "--port",
            str(port),
            "--count-file",
            str(count_file),
        ),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        _wait_for_tcp(port, process)
        yield port, count_file
    finally:
        _stop(process)


@contextmanager
def _websocket_target() -> Iterator[int]:
    port = _free_loopback_port()
    process = subprocess.Popen(
        (sys.executable, str(WEBSOCKET_TARGET), "--port", str(port)),
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
def _pipelock(tmp_path: Path, *, name: str) -> Iterator[int]:
    assert PIPELOCK_TEST_BIN is not None
    port = _free_loopback_port()
    home = tmp_path / f"home-{name}"
    home.mkdir()
    log_handle = (tmp_path / f"{name}.log").open("w", encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(home)
    process = subprocess.Popen(
        (
            PIPELOCK_TEST_BIN,
            "run",
            "--config",
            str(BYPASS_CONFIG),
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
        yield port
    finally:
        _stop(process)
        log_handle.close()


def _direct_get(target: str) -> tuple[int, str]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(target, timeout=5) as response:
        return int(response.status), response.read().decode("utf-8", errors="replace")


def _mediated_fetch(proxy_port: int, target: str) -> tuple[int, str]:
    query = urllib.parse.urlencode({"url": target})
    request_url = f"http://127.0.0.1:{proxy_port}/fetch?{query}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request_url, timeout=5) as response:
            return int(response.status), response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")


def _target_count(path: Path) -> int:
    return int(path.read_text(encoding="utf-8"))


def _direct_egress_invocation() -> CapabilityInvocation:
    project_id = new_id("project")
    correlation_id = "pipelock-bypass-stdio-730"
    return CapabilityInvocation(
        invocation_id="pipelock-bypass-stdio-730",
        capability_id="tool.direct_fetch",
        arguments={},
        context=OperationContext(
            correlation_id=correlation_id,
            owner_type="user",
            owner_id="user-730",
            project_id=project_id,
        ),
        trace=InvocationTrace(
            correlation_id=correlation_id,
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=project_id,
        ),
    )


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_uncontained_http_client_can_bypass_mediated_private_target_block(tmp_path: Path) -> None:
    with _http_target(tmp_path) as (target_port, count_file):
        target = f"http://127.0.0.1:{target_port}/direct"
        with _pipelock(tmp_path, name="http-bypass") as proxy_port:
            mediated_status, _mediated_body = _mediated_fetch(proxy_port, target)

        assert mediated_status >= 400
        assert _target_count(count_file) == 0

        direct_status, direct_body = _direct_get(target)
        assert direct_status == 200
        assert TARGET_SENTINEL in direct_body
        assert _target_count(count_file) == 1


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None or PIPELOCK_TEST_CONFIG is None,
    reason="requires the pinned Pipelock #730 compatibility runtime and generated audit config",
)
def test_wrapped_mcp_stdio_child_can_open_direct_network_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert PIPELOCK_TEST_BIN is not None
    assert PIPELOCK_TEST_CONFIG is not None

    with _http_target(tmp_path) as (target_port, count_file):
        target = f"http://127.0.0.1:{target_port}/mcp-child"
        monkeypatch.setenv("PIPELOCK_730_BYPASS_TARGET", target)

        async def scenario() -> None:
            config = MCPServerConfig(
                server_id="pipelock-stdio-direct-egress",
                command=(
                    PIPELOCK_TEST_BIN,
                    "mcp",
                    "proxy",
                    "--config",
                    PIPELOCK_TEST_CONFIG,
                    "--",
                    sys.executable,
                    str(MCP_DIRECT_EGRESS),
                ),
                read_timeout_seconds=15,
                capability_id_overrides={"direct_fetch": "tool.direct_fetch"},
            )
            registry = CapabilityRegistry()
            await registry.register_provider(build_mcp_provider(config))
            result = await CapabilityInvoker(registry).invoke(_direct_egress_invocation())

            assert result.output == {
                "status": 200,
                "body": TARGET_SENTINEL,
                "transport": "direct-child",
            }

        asyncio.run(scenario())
        assert _target_count(count_file) == 1


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_uncontained_websocket_client_can_bypass_mediated_private_target_block(
    tmp_path: Path,
) -> None:
    websockets = pytest.importorskip("websockets")

    async def proxied_round_trip(proxy_port: int, target_port: int) -> bool:
        target = f"ws://127.0.0.1:{target_port}/echo"
        proxy_url = (
            f"ws://127.0.0.1:{proxy_port}/ws?"
            + urllib.parse.urlencode({"url": target})
        )
        try:
            async with websockets.connect(
                proxy_url,
                compression=None,
                open_timeout=5,
                close_timeout=2,
            ) as websocket:
                await websocket.send("mediated")
                response = await asyncio.wait_for(websocket.recv(), timeout=2)
                return response == "mediated"
        except Exception:
            return False

    async def direct_round_trip(target_port: int) -> str:
        async with websockets.connect(
            f"ws://127.0.0.1:{target_port}/echo",
            compression=None,
            open_timeout=5,
            close_timeout=2,
        ) as websocket:
            await websocket.send("direct")
            return str(await asyncio.wait_for(websocket.recv(), timeout=2))

    with _websocket_target() as target_port:
        with _pipelock(tmp_path, name="websocket-bypass") as proxy_port:
            assert asyncio.run(proxied_round_trip(proxy_port, target_port)) is False

        assert asyncio.run(direct_round_trip(target_port)) == "direct"
