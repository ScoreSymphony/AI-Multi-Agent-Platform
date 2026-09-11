from __future__ import annotations

from typing import Any

import pytest

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig
from ai_multi_agent_platform.adapters.mcp_stateless import (
    MCP_STATELESS_PROTOCOL_REVISION,
    MCPStatelessHTTPClient,
    _WireResponse,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue


class _ScriptedStatelessClient(MCPStatelessHTTPClient):
    def __init__(self, responses: list[_WireResponse]) -> None:
        super().__init__(MCPServerConfig(server_id="test", endpoint="http://localhost.invalid/mcp"))
        self.responses = responses
        self.requests: list[dict[str, JsonValue]] = []

    def _post_json(self, body: dict[str, JsonValue]) -> _WireResponse:
        self.requests.append(body)
        if not self.responses:
            raise AssertionError("unexpected MCP request")
        return self.responses.pop(0)


def _result(payload: dict[str, Any]) -> _WireResponse:
    return _WireResponse(
        status=200,
        payload={"jsonrpc": "2.0", "id": 1, "result": payload},
    )


def _unsupported(*supported: str) -> _WireResponse:
    return _WireResponse(
        status=400,
        payload={
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32022,
                "message": "Unsupported protocol version",
                "data": {
                    "supported": list(supported),
                    "requested": MCP_STATELESS_PROTOCOL_REVISION,
                },
            },
        },
    )


@pytest.mark.asyncio
async def test_stateless_client_populates_required_metadata_on_every_request() -> None:
    client = _ScriptedStatelessClient([_result({"tools": []})])

    assert await client.list_tools() == ()

    assert len(client.requests) == 1
    params = client.requests[0]["params"]
    assert isinstance(params, dict)
    meta = params["_meta"]
    assert isinstance(meta, dict)
    assert meta["io.modelcontextprotocol/protocolVersion"] == MCP_STATELESS_PROTOCOL_REVISION
    assert meta["io.modelcontextprotocol/clientCapabilities"] == {}
    assert meta["io.modelcontextprotocol/clientInfo"] == {
        "name": "ai-multi-agent-platform",
        "version": "0.0.1",
    }


@pytest.mark.asyncio
async def test_stateless_client_retries_supported_version_rejection_once() -> None:
    client = _ScriptedStatelessClient(
        [
            _unsupported(MCP_STATELESS_PROTOCOL_REVISION),
            _result({"tools": []}),
        ]
    )

    assert await client.list_tools() == ()
    assert len(client.requests) == 2
    assert client.requests[0]["id"] != client.requests[1]["id"]


@pytest.mark.asyncio
async def test_stateless_client_rejects_no_common_protocol_revision() -> None:
    client = _ScriptedStatelessClient([_unsupported("2025-11-25")])

    with pytest.raises(ContractError) as exc_info:
        await client.list_tools()

    assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
    assert exc_info.value.details == {
        "requested_protocol_revision": MCP_STATELESS_PROTOCOL_REVISION,
        "supported_protocol_revisions": ["2025-11-25"],
    }
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_stateless_client_lists_and_calls_tools_without_stateful_initialize() -> None:
    client = _ScriptedStatelessClient(
        [
            _result(
                {
                    "tools": [
                        {
                            "name": "add_numbers",
                            "description": "Add two numbers",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "a": {"type": "number"},
                                    "b": {"type": "number"},
                                },
                            },
                        }
                    ],
                    "resultType": "complete",
                    "ttlMs": 0,
                    "cacheScope": "private",
                }
            ),
            _result(
                {
                    "content": [{"type": "text", "text": "The sum of 2 and 3 is 5"}],
                    "resultType": "complete",
                }
            ),
        ]
    )

    tools = await client.list_tools()
    assert [tool.name for tool in tools] == ["add_numbers"]
    result = await client.call_tool("add_numbers", {"a": 2, "b": 3})

    assert isinstance(result, dict)
    assert result["resultType"] == "complete"
    assert client.requests[0]["method"] == "tools/list"
    assert client.requests[1]["method"] == "tools/call"
    assert client.requests[1]["params"] == {
        "name": "add_numbers",
        "arguments": {"a": 2, "b": 3},
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": MCP_STATELESS_PROTOCOL_REVISION,
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {
                "name": "ai-multi-agent-platform",
                "version": "0.0.1",
            },
        },
    }


def test_stateless_client_rejects_stdio_and_other_protocol_revisions() -> None:
    stdio = MCPServerConfig(server_id="stdio", command=("python", "server.py"))
    with pytest.raises(ValueError, match="requires an HTTP endpoint"):
        MCPStatelessHTTPClient(stdio)

    endpoint = MCPServerConfig(server_id="http", endpoint="http://localhost.invalid/mcp")
    with pytest.raises(ValueError, match="supports only protocol revision"):
        MCPStatelessHTTPClient(endpoint, protocol_revision="2025-11-25")
