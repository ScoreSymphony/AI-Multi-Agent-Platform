from __future__ import annotations

from mcp.server import MCPServer

mcp = MCPServer("issue-12-stdio-test")


@mcp.tool()
def lookup(query: str) -> dict[str, str]:
    """Return a deterministic lookup payload for transport integration tests."""

    return {"query": query, "transport": "stdio"}


if __name__ == "__main__":
    mcp.run()
