"""Local Streamable-HTTP MCP fixture for profile-matched platform conformance tests."""

from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("platform-mcp-http-fixture")


@mcp.tool()
def lookup(query: str) -> dict[str, str]:
    """Return a deterministic payload for the canonical capability path."""

    return {"query": query, "transport": "streamable-http"}


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=int(os.environ["MCP_FIXTURE_PORT"]),
        json_response=True,
    )
