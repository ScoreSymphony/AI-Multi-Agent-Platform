from __future__ import annotations

import argparse
import socket

from mcp.server import MCPServer

mcp = MCPServer("issue-730-stdio-network-probe")
_TARGET_HOST = "127.0.0.1"
_TARGET_PORT = 0


@mcp.tool()
def lookup(query: str) -> dict[str, str]:
    """Return a deterministic payload proving stdio remains usable."""

    return {"query": query, "transport": "stdio"}


@mcp.tool()
def probe_direct_network(protocol: str) -> dict[str, object]:
    """Attempt one evaluation-only direct connection from the MCP child process."""

    if protocol not in {"raw", "http", "mcp_http", "websocket"}:
        raise ValueError(f"unsupported probe protocol: {protocol}")

    request = {
        "raw": b"issue-730-raw\n",
        "http": b"GET /issue-730-http HTTP/1.1\r\nHost: fixture\r\nConnection: close\r\n\r\n",
        "mcp_http": (
            b"POST /mcp HTTP/1.1\r\nHost: fixture\r\nContent-Type: application/json\r\n"
            b"Content-Length: 2\r\nConnection: close\r\n\r\n{}"
        ),
        "websocket": (
            b"GET /issue-730-ws HTTP/1.1\r\nHost: fixture\r\nUpgrade: websocket\r\n"
            b"Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
        ),
    }[protocol]

    try:
        with socket.create_connection((_TARGET_HOST, _TARGET_PORT), timeout=2) as connection:
            connection.sendall(request)
        return {"protocol": protocol, "connected": True, "errno": None}
    except OSError as exc:
        return {
            "protocol": protocol,
            "connected": False,
            "errno": exc.errno,
            "error": type(exc).__name__,
        }


def main() -> None:
    global _TARGET_HOST, _TARGET_PORT

    parser = argparse.ArgumentParser()
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", required=True, type=int)
    args = parser.parse_args()
    _TARGET_HOST = args.target_host
    _TARGET_PORT = args.target_port
    mcp.run()


if __name__ == "__main__":
    main()
