from __future__ import annotations

import asyncio
import errno
import os
import socketserver
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

PIPELOCK_TEST_BIN = os.getenv("PIPELOCK_730_BIN")
PIPELOCK_TEST_CONFIG = os.getenv("PIPELOCK_730_CONFIG")
FIXTURE_DIR = Path(__file__).parents[2] / "fixtures"
MCP_NETWORK_PROBE = FIXTURE_DIR / "mcp_stdio_network_probe_server.py"
INET_SOCKET_WRAPPER = (
    Path(__file__).parents[2] / ".." / "scripts" / "ci" / "issue730_inet_socket_deny_exec.py"
)


@contextmanager
def _tcp_target() -> Iterator[tuple[int, list[bytes]]]:
    records: list[bytes] = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            records.append(self.request.recv(4096))

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield int(server.server_address[1]), records
        finally:
            server.shutdown()
            thread.join(timeout=5)


def _wait_for_record_count(records: list[bytes], expected: int) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if len(records) >= expected:
            return
        time.sleep(0.02)
    assert len(records) >= expected


async def _invoke_probe(
    *,
    target_port: int,
    contained: bool,
    protocols: tuple[str, ...],
) -> list[dict[str, object]]:
    from ai_multi_agent_platform.adapters.mcp import MCPServerConfig
    from ai_multi_agent_platform.adapters.mcp_sdk import build_mcp_provider
    from ai_multi_agent_platform.capabilities import (
        CapabilityInvocation,
        CapabilityInvoker,
        CapabilityRegistry,
        InvocationTrace,
    )
    from ai_multi_agent_platform.contracts import OperationContext
    from ai_multi_agent_platform.domain import new_id

    assert PIPELOCK_TEST_BIN is not None
    assert PIPELOCK_TEST_CONFIG is not None

    child_command: list[str] = [
        sys.executable,
        str(MCP_NETWORK_PROBE),
        "--target-host",
        "127.0.0.1",
        "--target-port",
        str(target_port),
    ]
    if contained:
        child_command = [
            sys.executable,
            str(INET_SOCKET_WRAPPER.resolve()),
            "--",
            *child_command,
        ]

    config = MCPServerConfig(
        server_id="pipelock-containment" if contained else "pipelock-uncontained",
        command=(
            PIPELOCK_TEST_BIN,
            "mcp",
            "proxy",
            "--config",
            PIPELOCK_TEST_CONFIG,
            "--",
            *child_command,
        ),
        read_timeout_seconds=15,
        capability_id_overrides={
            "lookup": "tool.lookup",
            "probe_direct_network": "tool.probe-direct-network",
        },
    )
    registry = CapabilityRegistry()
    await registry.register_provider(build_mcp_provider(config))
    invoker = CapabilityInvoker(registry)
    project_id = new_id("project")

    async def invoke(capability_id: str, arguments: dict[str, object]) -> dict[str, object]:
        correlation_id = new_id("correlation")
        invocation = CapabilityInvocation(
            invocation_id=new_id("invocation"),
            capability_id=capability_id,
            arguments=arguments,
            context=OperationContext(
                correlation_id=correlation_id,
                owner_type="user",
                owner_id="user-730-containment",
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
        result = await invoker.invoke(invocation)
        assert isinstance(result.output, dict)
        return result.output

    lookup = await invoke("tool.lookup", {"query": "containment-stdio-still-works"})
    assert lookup == {"query": "containment-stdio-still-works", "transport": "stdio"}

    return [
        await invoke("tool.probe-direct-network", {"protocol": protocol}) for protocol in protocols
    ]


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None or PIPELOCK_TEST_CONFIG is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_pipelock_mcp_proxy_alone_does_not_contain_stdio_child_direct_socket() -> None:
    with _tcp_target() as (target_port, records):
        results = asyncio.run(
            _invoke_probe(target_port=target_port, contained=False, protocols=("raw",))
        )
        _wait_for_record_count(records, 1)

    assert results == [{"protocol": "raw", "connected": True, "errno": None}]
    assert len(records) == 1
    assert records[0].startswith(b"issue-730-raw")


@pytest.mark.integration
@pytest.mark.skipif(
    PIPELOCK_TEST_BIN is None or PIPELOCK_TEST_CONFIG is None,
    reason="requires the pinned Pipelock #730 compatibility runtime",
)
def test_seccomp_contained_stdio_child_blocks_direct_network_protocols() -> None:
    protocols = ("raw", "http", "mcp_http", "websocket")
    with _tcp_target() as (target_port, records):
        results = asyncio.run(
            _invoke_probe(target_port=target_port, contained=True, protocols=protocols)
        )

    assert records == []
    assert [result["protocol"] for result in results] == list(protocols)
    for result in results:
        assert result["connected"] is False
        assert result["errno"] == errno.EPERM
        assert result["error"] == "PermissionError"
