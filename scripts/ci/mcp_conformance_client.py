"""Client harness launched by the official MCP conformance runner.

The stable revision is exercised through the official Python SDK adapter. The newer
2026-07-28 revision is exercised through the explicit platform stateless HTTP adapter so
stateful and stateless protocol families remain independently testable.
"""

from __future__ import annotations

import asyncio
import os
import sys

from ai_multi_agent_platform.adapters.mcp import MCPClient, MCPServerConfig
from ai_multi_agent_platform.adapters.mcp_sdk import MCPPythonSDKClient
from ai_multi_agent_platform.adapters.mcp_stateless import (
    MCP_STATELESS_PROTOCOL_REVISION,
    MCPStatelessHTTPClient,
)

_STATEFUL_PROTOCOL_REVISION = "2025-11-25"
_TRACK_PROTOCOL_ENV = "AI_MULTI_AGENT_PLATFORM_MCP_CONFORMANCE_PROTOCOL_REVISION"


def _client(server_url: str, protocol_revision: str) -> MCPClient:
    config = MCPServerConfig(
        server_id="official-conformance",
        endpoint=server_url,
        read_timeout_seconds=10.0,
        protocol_revision=(
            protocol_revision if protocol_revision == _STATEFUL_PROTOCOL_REVISION else None
        ),
    )
    if protocol_revision == _STATEFUL_PROTOCOL_REVISION:
        return MCPPythonSDKClient(config)
    if protocol_revision == MCP_STATELESS_PROTOCOL_REVISION:
        return MCPStatelessHTTPClient(config, protocol_revision=protocol_revision)
    raise RuntimeError(f"unsupported MCP protocol revision: {protocol_revision}")


async def _run(server_url: str, scenario: str, protocol_revision: str) -> None:
    client = _client(server_url, protocol_revision)

    if scenario == "initialize":
        if protocol_revision != _STATEFUL_PROTOCOL_REVISION:
            raise RuntimeError("initialize is not part of the stateless MCP lifecycle")
        if not await client.ping():
            raise RuntimeError("MCP initialization/negotiation did not become healthy")
        return

    if scenario in {"request-metadata", "request_metadata"}:
        if protocol_revision != MCP_STATELESS_PROTOCOL_REVISION:
            raise RuntimeError("request-metadata applies only to the stateless MCP revision")
        await client.list_tools()
        return

    if scenario in {"tools_call", "tools-call"}:
        tools = await client.list_tools()
        if not tools:
            raise RuntimeError("official conformance server exposed no tools")
        tool = next((item for item in tools if item.name == "add_numbers"), tools[0])
        await client.call_tool(tool.name, {"a": 2, "b": 3})
        return

    raise RuntimeError(f"unsupported MCP conformance client scenario: {scenario}")


def _protocol_revision() -> str:
    expected = os.environ.get(_TRACK_PROTOCOL_ENV, "").strip()
    upstream = os.environ.get("MCP_CONFORMANCE_PROTOCOL_VERSION", "").strip()
    if not expected:
        raise RuntimeError(f"{_TRACK_PROTOCOL_ENV} is missing")
    if upstream and upstream != expected:
        raise RuntimeError(
            "official MCP runner protocol revision does not match pinned track: "
            f"runner={upstream!r}, track={expected!r}"
        )
    return upstream or expected


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("official MCP conformance runner did not supply a server URL", file=sys.stderr)
        return 2

    server_url = args[-1]
    scenario = os.environ.get("MCP_CONFORMANCE_SCENARIO", "").strip()
    if not scenario:
        print("MCP_CONFORMANCE_SCENARIO is missing", file=sys.stderr)
        return 2

    try:
        protocol_revision = _protocol_revision()
        asyncio.run(_run(server_url, scenario, protocol_revision))
    except Exception as exc:
        print(f"MCP conformance client failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
