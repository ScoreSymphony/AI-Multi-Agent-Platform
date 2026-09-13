"""MCP SDK transport integration coverage migrated from the historical root suite."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

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

TESTS_DIR = Path(__file__).parents[2]
FIXTURE_SERVER = TESTS_DIR / "fixtures" / "mcp_stdio_server.py"
HTTP_FIXTURE_SERVER = TESTS_DIR / "fixtures" / "mcp_http_server.py"
_STABLE_PROTOCOL_REVISION = "2025-11-25"


def _request() -> CapabilityInvocation:
    project_id = new_id("project")
    return CapabilityInvocation(
        invocation_id="mcp-real-1",
        capability_id="tool.lookup",
        arguments={"query": "real-transport"},
        context=OperationContext(
            correlation_id="mcp-correlation-1",
            owner_type="user",
            owner_id="user-1",
            project_id=project_id,
        ),
        trace=InvocationTrace(
            correlation_id="mcp-correlation-1",
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=project_id,
        ),
    )


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_local_server(process: subprocess.Popen[str], port: int) -> None:
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"MCP HTTP fixture exited before becoming ready:\nstdout={stdout}\nstderr={stderr}"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("MCP HTTP fixture did not become ready")


def test_official_mcp_sdk_stdio_transport_uses_canonical_invocation_path() -> None:
    async def scenario() -> None:
        config = MCPServerConfig(
            server_id="real-stdio",
            command=(sys.executable, str(FIXTURE_SERVER)),
            read_timeout_seconds=10,
            protocol_revision=_STABLE_PROTOCOL_REVISION,
            capability_id_overrides={"lookup": "tool.lookup"},
        )
        registry = CapabilityRegistry()
        await registry.register_provider(build_mcp_provider(config))

        result = await CapabilityInvoker(registry).invoke(_request())

        assert result.capability_id == "tool.lookup"
        assert result.provider_id == "mcp:real-stdio"
        assert result.output == {"query": "real-transport", "transport": "stdio"}

    asyncio.run(scenario())


def test_official_mcp_sdk_streamable_http_uses_exact_claimed_profile() -> None:
    port = _free_local_port()
    environment = {**os.environ, "MCP_FIXTURE_PORT": str(port)}
    process = subprocess.Popen(
        (sys.executable, str(HTTP_FIXTURE_SERVER)),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_local_server(process, port)

        async def scenario() -> None:
            config = MCPServerConfig(
                server_id="real-http",
                endpoint=f"http://127.0.0.1:{port}/mcp",
                read_timeout_seconds=10,
                protocol_revision=_STABLE_PROTOCOL_REVISION,
                capability_id_overrides={"lookup": "tool.lookup"},
            )
            registry = CapabilityRegistry()
            await registry.register_provider(build_mcp_provider(config))

            result = await CapabilityInvoker(registry).invoke(_request())

            assert result.capability_id == "tool.lookup"
            assert result.provider_id == "mcp:real-http"
            assert result.output == {
                "query": "real-transport",
                "transport": "streamable-http",
            }

        asyncio.run(scenario())
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
