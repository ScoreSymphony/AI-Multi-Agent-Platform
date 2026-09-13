from __future__ import annotations

import base64
from typing import Any

import pytest

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig
from ai_multi_agent_platform.adapters.mcp_stateless import MCPStatelessHTTPClient
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue


class _DiscoveryClient(MCPStatelessHTTPClient):
    def __init__(self, tools: list[dict[str, Any]]) -> None:
        super().__init__(
            MCPServerConfig(
                server_id="header-params",
                endpoint="http://localhost.invalid/mcp",
                enable_tasks=True,
            )
        )
        self._discovery_tools = tools

    async def _request(
        self,
        method: str,
        params: dict[str, JsonValue],
        *,
        client_extensions: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        assert method == "tools/list"
        return {"tools": self._discovery_tools}


def _tools_call_body(name: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments,
        },
    }


@pytest.mark.asyncio
async def test_x_mcp_header_mirrors_nested_primitive_values() -> None:
    client = _DiscoveryClient(
        [
            {
                "name": "execute_sql",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "region": {"type": "string", "x-mcp-header": "Region"},
                        "retry": {"type": "boolean", "x-mcp-header": "Retry"},
                        "options": {
                            "type": "object",
                            "properties": {
                                "tenant": {
                                    "type": "integer",
                                    "x-mcp-header": "Tenant-Id",
                                }
                            },
                        },
                    },
                },
            }
        ]
    )

    tools = await client.list_tools()
    headers = client._request_headers(
        _tools_call_body(
            "execute_sql",
            {
                "region": "us-west1",
                "retry": True,
                "options": {"tenant": 42},
            },
        )
    )

    assert [tool.name for tool in tools] == ["execute_sql"]
    assert headers["Mcp-Param-Region"] == "us-west1"
    assert headers["Mcp-Param-Retry"] == "true"
    assert headers["Mcp-Param-Tenant-Id"] == "42"


@pytest.mark.asyncio
async def test_x_mcp_header_omits_missing_and_null_values() -> None:
    client = _DiscoveryClient(
        [
            {
                "name": "lookup",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "tenant": {"type": "string", "x-mcp-header": "Tenant"},
                        "region": {"type": "string", "x-mcp-header": "Region"},
                    },
                },
            }
        ]
    )
    await client.list_tools()

    headers = client._request_headers(_tools_call_body("lookup", {"tenant": None}))

    assert "Mcp-Param-Tenant" not in headers
    assert "Mcp-Param-Region" not in headers


@pytest.mark.asyncio
async def test_x_mcp_header_uses_base64_sentinel_for_unsafe_values() -> None:
    client = _DiscoveryClient(
        [
            {
                "name": "lookup",
                "inputSchema": {
                    "type": "object",
                    "properties": {"greeting": {"type": "string", "x-mcp-header": "Greeting"}},
                },
            }
        ]
    )
    await client.list_tools()

    value = "Hello, 世界"
    headers = client._request_headers(_tools_call_body("lookup", {"greeting": value}))
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")

    assert headers["Mcp-Param-Greeting"] == f"=?base64?{encoded}?="


@pytest.mark.asyncio
async def test_x_mcp_header_uses_base64_sentinel_for_empty_string() -> None:
    client = _DiscoveryClient(
        [
            {
                "name": "lookup",
                "inputSchema": {
                    "type": "object",
                    "properties": {"label": {"type": "string", "x-mcp-header": "Label"}},
                },
            }
        ]
    )
    await client.list_tools()

    headers = client._request_headers(_tools_call_body("lookup", {"label": ""}))

    assert headers["Mcp-Param-Label"] == "=?base64??="


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_schema",
    [
        {"type": "string", "x-mcp-header": ""},
        {"type": "string", "x-mcp-header": "bad header"},
        {"type": "number", "x-mcp-header": "Rate"},
    ],
)
async def test_invalid_x_mcp_header_excludes_only_the_malformed_tool(
    invalid_schema: dict[str, object],
) -> None:
    client = _DiscoveryClient(
        [
            {
                "name": "invalid",
                "inputSchema": {
                    "type": "object",
                    "properties": {"value": invalid_schema},
                },
            },
            {
                "name": "valid",
                "inputSchema": {
                    "type": "object",
                    "properties": {"value": {"type": "string", "x-mcp-header": "Valid"}},
                },
            },
        ]
    )

    tools = await client.list_tools()

    assert [tool.name for tool in tools] == ["valid"]


@pytest.mark.asyncio
async def test_x_mcp_header_rejects_case_insensitive_duplicate_names() -> None:
    client = _DiscoveryClient(
        [
            {
                "name": "invalid",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "one": {"type": "string", "x-mcp-header": "Tenant"},
                        "two": {"type": "string", "x-mcp-header": "tenant"},
                    },
                },
            }
        ]
    )

    assert await client.list_tools() == ()


@pytest.mark.asyncio
async def test_x_mcp_header_rejects_annotation_outside_properties_chain() -> None:
    client = _DiscoveryClient(
        [
            {
                "name": "invalid",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "value": {
                            "type": "string",
                            "oneOf": [{"type": "string", "x-mcp-header": "Nested"}],
                        }
                    },
                },
            }
        ]
    )

    assert await client.list_tools() == ()


@pytest.mark.asyncio
async def test_x_mcp_header_rejects_out_of_range_integer_at_call_time() -> None:
    client = _DiscoveryClient(
        [
            {
                "name": "lookup",
                "inputSchema": {
                    "type": "object",
                    "properties": {"tenant": {"type": "integer", "x-mcp-header": "Tenant"}},
                },
            }
        ]
    )
    await client.list_tools()

    with pytest.raises(ContractError) as caught:
        client._request_headers(_tools_call_body("lookup", {"tenant": (2**53)}))

    assert caught.value.code is ErrorCode.INVALID_REQUEST
    assert caught.value.details["argument_path"] == "tenant"
