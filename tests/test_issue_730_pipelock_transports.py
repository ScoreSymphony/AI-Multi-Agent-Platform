from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

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
HTTP_FIXTURE = FIXTURE_DIR / "mcp_streamable_http_server.py"
WEBSOCKET_FIXTURE = FIXTURE_DIR / "mcp_websocket_server.py"


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_tcp(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"fixture exited before listening on port {port}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"fixture did not listen on port {port}")


@contextmanager
def _running_fixture(path: Path) -> Iterator[int]:
    port = _free_loopback_port()
    process = subprocess.Popen(
        [sys.executable, str(path), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_tcp(port, process)
        yield port
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _invocation(query: str) -> CapabilityInvocation:
    project_id = new_id("project")
    return CapabilityInvocation(
        invocation_id=f"mcp-pipelock-730-{query}",
        capability_id="tool.lookup",
        arguments={"query": query},
        context=OperationContext(
            correlation_id=f"mcp-pipelock-correlation-730-{query}",
            owner_type="user",
            owner_id="user-730",
            project_id=project_id,
        ),
        trace=InvocationTrace(
            correlation_id=f"mcp-pipelock-correlation-730-{query}",
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=project_id,
        ),
    )


async def _exercise_upstream(
    *,
    server_id: str,
    upstream: str,
    query: str,
    expected_transport: str,
) -> None:
    assert PIPELOCK_TEST_BIN is not None
    assert PIPELOCK_TEST_CONFIG is not None

    config = MCPServerConfig(
        server_id=server_id,
        command=(
            PIPELOCK_TEST_BIN,
            "mcp",
            "proxy",
            "--config",
            PIPELOCK_TEST_CONFIG,
            "--upstream",
            upstream,
        ),
        read_timeout_seconds=15,
        capability_id_overrides={"lookup": "tool.lookup"},
    )
    registry = CapabilityRegistry()
    await registry.register_provider(build_mcp_provider(config))

    result = await CapabilityInvoker(registry).invoke(_invocation(query))

    assert result.capability_id == "tool.lookup"
    assert result.provider_id == f"mcp:{server_id}"
    assert result.output == {"query": query, "transport": expected_transport}


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None or PIPELOCK_TEST_CONFIG is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_pipelock_mcp_streamable_http_upstream_uses_canonical_invocation_path() -> None:
    with _running_fixture(HTTP_FIXTURE) as port:
        asyncio.run(
            _exercise_upstream(
                server_id="pipelock-streamable-http",
                upstream=f"http://127.0.0.1:{port}/mcp",
                query="pipelock-streamable-http",
                expected_transport="streamable-http",
            )
        )


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None or PIPELOCK_TEST_CONFIG is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_pipelock_mcp_websocket_upstream_uses_canonical_invocation_path() -> None:
    with _running_fixture(WEBSOCKET_FIXTURE) as port:
        asyncio.run(
            _exercise_upstream(
                server_id="pipelock-websocket",
                upstream=f"ws://127.0.0.1:{port}/mcp",
                query="pipelock-websocket",
                expected_transport="websocket",
            )
        )
