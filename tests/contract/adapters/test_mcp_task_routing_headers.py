from __future__ import annotations

import base64

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig
from ai_multi_agent_platform.adapters.mcp_stateless import MCPStatelessHTTPClient


def _client() -> MCPStatelessHTTPClient:
    return MCPStatelessHTTPClient(
        MCPServerConfig(
            server_id="routing-test",
            endpoint="http://localhost.invalid/mcp",
            enable_tasks=True,
        )
    )


def test_all_stateless_requests_use_required_transport_headers() -> None:
    client = _client()

    headers = client._request_headers(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "server/discover",
            "params": {},
        }
    )

    assert headers["Accept"] == "application/json, text/event-stream"
    assert headers["MCP-Protocol-Version"] == "2026-07-28"
    assert headers["Mcp-Method"] == "server/discover"
    assert "Mcp-Name" not in headers


def test_tools_call_uses_required_routing_headers() -> None:
    client = _client()

    headers = client._request_headers(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {}},
        }
    )

    assert headers["Mcp-Method"] == "tools/call"
    assert headers["Mcp-Name"] == "search"


def test_task_http_methods_use_required_routing_headers() -> None:
    client = _client()

    for method in ("tasks/get", "tasks/update", "tasks/cancel"):
        headers = client._request_headers(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": {"taskId": "task-route-secret"},
            }
        )
        assert headers["Mcp-Name"] == "task-route-secret"
        assert headers["Mcp-Method"] == method


def test_mcp_name_uses_base64_sentinel_for_unsafe_values() -> None:
    client = _client()

    for value in ("Hello, 世界", " padded ", "=?base64?literal?="):
        headers = client._request_headers(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tasks/get",
                "params": {"taskId": value},
            }
        )
        encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
        assert headers["Mcp-Name"] == f"=?base64?{encoded}?="
