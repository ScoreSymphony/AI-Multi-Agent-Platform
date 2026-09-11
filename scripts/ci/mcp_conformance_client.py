"""Client harness launched by the official MCP conformance runner.

The harness deliberately uses the platform MCP SDK adapter for the currently claimed
stateful tool-client profile. Newer stateless revisions fail explicitly until the adapter
implements and claims those semantics; CI records that prerelease evidence separately.
"""

from __future__ import annotations

import asyncio
import os
import sys

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig
from ai_multi_agent_platform.adapters.mcp_sdk import MCPPythonSDKClient

_CLAIMED_PROTOCOL_REVISION = "2025-11-25"


async def _run(server_url: str, scenario: str, protocol_revision: str) -> None:
    if protocol_revision != _CLAIMED_PROTOCOL_REVISION:
        raise RuntimeError(
            "platform MCP client does not claim protocol revision "
            f"{protocol_revision!r}; claimed revision is {_CLAIMED_PROTOCOL_REVISION!r}"
        )

    client = MCPPythonSDKClient(
        MCPServerConfig(
            server_id="official-conformance",
            endpoint=server_url,
            read_timeout_seconds=10.0,
        )
    )

    if scenario == "initialize":
        if not await client.ping():
            raise RuntimeError("MCP initialization/negotiation did not become healthy")
        return

    if scenario in {"tools_call", "tools-call"}:
        tools = await client.list_tools()
        if not tools:
            raise RuntimeError("official conformance server exposed no tools")
        tool = next((item for item in tools if item.name == "add_numbers"), tools[0])
        await client.call_tool(tool.name, {"a": 2, "b": 3})
        return

    raise RuntimeError(f"unsupported MCP conformance client scenario: {scenario}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("official MCP conformance runner did not supply a server URL", file=sys.stderr)
        return 2

    server_url = args[-1]
    scenario = os.environ.get("MCP_CONFORMANCE_SCENARIO", "").strip()
    protocol_revision = os.environ.get("MCP_CONFORMANCE_PROTOCOL_VERSION", "").strip()
    if not scenario:
        print("MCP_CONFORMANCE_SCENARIO is missing", file=sys.stderr)
        return 2
    if not protocol_revision:
        print("MCP_CONFORMANCE_PROTOCOL_VERSION is missing", file=sys.stderr)
        return 2

    try:
        asyncio.run(_run(server_url, scenario, protocol_revision))
    except Exception as exc:
        print(f"MCP conformance client failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
