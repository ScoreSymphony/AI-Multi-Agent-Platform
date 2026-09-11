from __future__ import annotations

import argparse

from mcp.server import MCPServer

mcp = MCPServer("issue-730-streamable-http-test")


@mcp.tool()
def lookup(query: str) -> dict[str, str]:
    """Return a deterministic lookup payload for Pipelock transport tests."""

    return {"query": query, "transport": "streamable-http"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    mcp.run(
        transport="streamable-http",
        host=args.host,
        port=args.port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
