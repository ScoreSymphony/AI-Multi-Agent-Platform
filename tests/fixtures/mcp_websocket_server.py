from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from websockets.asyncio.server import ServerConnection, serve


async def handle(connection: ServerConnection) -> None:
    async for raw_message in connection:
        message = json.loads(raw_message)
        if not isinstance(message, dict) or "id" not in message:
            continue

        request_id = message["id"]
        method = message.get("method")
        if method == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "issue-730-websocket-test", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "lookup",
                        "description": "Return a deterministic WebSocket fixture payload.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                            "additionalProperties": False,
                        },
                    }
                ]
            }
        elif method == "tools/call":
            params = message.get("params") or {}
            arguments = params.get("arguments") or {}
            query = str(arguments.get("query", ""))
            structured = {"query": query, "transport": "websocket"}
            result = {
                "content": [{"type": "text", "text": json.dumps(structured, sort_keys=True)}],
                "structuredContent": structured,
                "isError": False,
            }
        else:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"unsupported method: {method}"},
            }
            await connection.send(json.dumps(response))
            continue

        await connection.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}))


async def run(host: str, port: int) -> None:
    async with serve(handle, host, port):
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.host, args.port))


if __name__ == "__main__":
    main()
