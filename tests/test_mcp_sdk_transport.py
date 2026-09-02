from __future__ import annotations

import asyncio
import sys
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

FIXTURE_SERVER = Path(__file__).parent / "fixtures" / "mcp_stdio_server.py"


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


def test_official_mcp_sdk_stdio_transport_uses_canonical_invocation_path() -> None:
    async def scenario() -> None:
        config = MCPServerConfig(
            server_id="real-stdio",
            command=(sys.executable, str(FIXTURE_SERVER)),
            read_timeout_seconds=10,
            capability_id_overrides={"lookup": "tool.lookup"},
        )
        registry = CapabilityRegistry()
        await registry.register_provider(build_mcp_provider(config))

        result = await CapabilityInvoker(registry).invoke(_request())

        assert result.capability_id == "tool.lookup"
        assert result.provider_id == "mcp:real-stdio"
        assert result.output == {"query": "real-transport", "transport": "stdio"}

    asyncio.run(scenario())


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
