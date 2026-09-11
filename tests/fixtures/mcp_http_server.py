"""Local Streamable-HTTP MCP fixture for profile-matched platform conformance tests."""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("platform-mcp-http-fixture", json_response=True)


@mcp.tool()
def lookup(query: str) -> dict[str, str]:
    """Return a deterministic payload for the canonical capability path."""

    return {"query": query, "transport": "streamable-http"}


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=int(os.environ["MCP_FIXTURE_PORT"]),
        streamable_http_path="/mcp",
        json_response=True,
    )
