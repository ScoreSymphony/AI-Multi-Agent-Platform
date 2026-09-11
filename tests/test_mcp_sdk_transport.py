from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig
from ai_multi_agent_platform.adapters.mcp_sdk import MCPPythonSDKClient, build_mcp_provider
from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistry,
    InvocationTrace,
)
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id

FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"
HTTP_FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "mcp_http_server.py"
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
                "MCP HTTP fixture exited before becoming ready:\n"
                f"stdout={stdout}\nstderr={stderr}"
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


class _FakeConnectedClient:
    def __init__(self, protocol_version: str) -> None:
        self.protocol_version = protocol_version
        self.entered = 0
        self.exited = 0

    async def __aenter__(self) -> _FakeConnectedClient:
        self.entered += 1
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.exited += 1


@pytest.mark.parametrize(
    "negotiated_revision",
    ("2024-11-05", "2026-07-28", "not-a-protocol-revision", ""),
)
def test_stable_sdk_profile_rejects_incompatible_or_malformed_negotiation(
    negotiated_revision: str,
) -> None:
    client = MCPPythonSDKClient(
        MCPServerConfig(
            server_id="negative-negotiation",
            endpoint="http://127.0.0.1:1/mcp",
            protocol_revision=_STABLE_PROTOCOL_REVISION,
        )
    )
    fake = _FakeConnectedClient(negotiated_revision)
    client._client = lambda: fake  # type: ignore[method-assign]

    assert asyncio.run(client.ping()) is False
    assert fake.entered == 1
    assert fake.exited == 1


def test_stable_sdk_profile_accepts_exact_revision_and_closes_session() -> None:
    client = MCPPythonSDKClient(
        MCPServerConfig(
            server_id="exact-negotiation",
            endpoint="http://127.0.0.1:1/mcp",
            protocol_revision=_STABLE_PROTOCOL_REVISION,
        )
    )
    fake = _FakeConnectedClient(_STABLE_PROTOCOL_REVISION)
    client._client = lambda: fake  # type: ignore[method-assign]

    assert asyncio.run(client.ping()) is True
    assert fake.entered == 1
    assert fake.exited == 1


def test_mcp_config_rejects_ambiguous_transport_targets() -> None:
    try:
        MCPServerConfig(
            server_id="ambiguous",
            endpoint="http://127.0.0.1:9000/mcp",
            command=(sys.executable, str(FIXTURE_SERVER)),
        )
    except ValueError as exc:
        assert "exactly one" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("ambiguous MCP transport configuration was accepted")
