from __future__ import annotations

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig
from ai_multi_agent_platform.adapters.mcp_stateless import MCPStatelessHTTPClient


def test_task_http_methods_use_required_routing_headers() -> None:
    client = MCPStatelessHTTPClient(
        MCPServerConfig(
            server_id="routing-test",
            endpoint="http://localhost.invalid/mcp",
            enable_tasks=True,
        )
    )

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
