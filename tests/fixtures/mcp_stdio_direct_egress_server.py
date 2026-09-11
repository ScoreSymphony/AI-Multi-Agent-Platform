from __future__ import annotations

import os
import urllib.request

from mcp.server import MCPServer

mcp = MCPServer("issue-730-stdio-direct-egress")


@mcp.tool()
def direct_fetch() -> dict[str, str | int]:
    """Reach the controlled target directly from the wrapped child process."""

    target = os.environ["PIPELOCK_730_BYPASS_TARGET"]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(target, timeout=5) as response:
        body = response.read().decode("utf-8", errors="replace")
        return {
            "status": int(response.status),
            "body": body,
            "transport": "direct-child",
        }


if __name__ == "__main__":
    mcp.run()
