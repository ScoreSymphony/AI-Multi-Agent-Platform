from __future__ import annotations

from typing import Any

import pytest

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig
from ai_multi_agent_platform.adapters.mcp_stateless import MCPStatelessHTTPClient, _WireResponse
from ai_multi_agent_platform.adapters.mcp_tasks import MCPTaskStarted, MCPTaskStatus
from ai_multi_agent_platform.contracts.types import JsonValue


class _SeedClient(MCPStatelessHTTPClient):
    def __init__(self, responses: list[_WireResponse]) -> None:
        super().__init__(
            MCPServerConfig(
                server_id="seed-test",
                endpoint="http://localhost.invalid/mcp",
                enable_tasks=True,
            )
        )
        self.responses = responses
        self.requests: list[dict[str, JsonValue]] = []

    def _post_json(self, body: dict[str, JsonValue]) -> _WireResponse:
        self.requests.append(body)
        if not self.responses:
            raise AssertionError("unexpected MCP request")
        return self.responses.pop(0)


def _result(payload: dict[str, Any], *, request_id: int) -> _WireResponse:
    return _WireResponse(
        status=200,
        payload={"jsonrpc": "2.0", "id": request_id, "result": payload},
    )


@pytest.mark.asyncio
async def test_terminal_create_task_seed_is_resolved_through_detailed_tasks_get() -> None:
    client = _SeedClient(
        [
            _result(
                {
                    "resultType": "task",
                    "taskId": "seed-terminal-task",
                    "status": "completed",
                    "createdAt": "2026-09-13T19:00:00Z",
                    "lastUpdatedAt": "2026-09-13T19:00:01Z",
                    "ttlMs": 60_000,
                },
                request_id=1,
            ),
            _result(
                {
                    "resultType": "complete",
                    "taskId": "seed-terminal-task",
                    "status": "completed",
                    "createdAt": "2026-09-13T19:00:00Z",
                    "lastUpdatedAt": "2026-09-13T19:00:01Z",
                    "ttlMs": 60_000,
                    "result": {
                        "structuredContent": {"answer": 42},
                        "isError": False,
                    },
                },
                request_id=2,
            ),
        ]
    )

    outcome = await client.call_tool_with_tasks("lookup", {"query": "abc"})

    assert isinstance(outcome, MCPTaskStarted)
    assert outcome.task.status is MCPTaskStatus.COMPLETED
    assert outcome.task.result == {
        "structuredContent": {"answer": 42},
        "isError": False,
    }
    assert [request["method"] for request in client.requests] == ["tools/call", "tasks/get"]
