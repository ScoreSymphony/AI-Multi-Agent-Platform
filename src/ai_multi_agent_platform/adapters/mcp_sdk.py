"""Optional concrete MCP transport implemented with the official Python SDK.

This module is intentionally adapter-scoped. Importing the platform core never imports
``mcp`` and the baseline package does not require the MCP extra.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .mcp import MCPClient, MCPServerConfig, MCPTool, MCPToolProvider


class MCPPythonSDKClient(MCPClient):
    """Concrete MCP client supporting Streamable HTTP and stdio subprocesses."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config

    def _target(self) -> str | StdioServerParameters:
        if self._config.endpoint is not None:
            return self._config.endpoint
        executable, *args = self._config.command
        return StdioServerParameters(
            command=executable,
            args=list(args),
            env=dict(self._config.environment) or None,
            cwd=self._config.cwd,
        )

    def _client(self) -> Client:
        return Client(
            self._target(),
            read_timeout_seconds=self._config.read_timeout_seconds,
        )

    async def list_tools(self) -> tuple[MCPTool, ...]:
        async with self._client() as client:
            listed = await client.list_tools()
            return tuple(
                MCPTool(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=_json_object(tool.input_schema),
                    output_schema=(
                        _json_object(tool.output_schema) if tool.output_schema is not None else None
                    ),
                )
                for tool in listed.tools
            )

    async def call_tool(self, name: str, arguments: dict[str, JsonValue]) -> JsonValue:
        async with self._client() as client:
            result = await client.call_tool(name, arguments)
            if result.is_error:
                raise ContractError(
                    ErrorCode.BACKEND_ERROR,
                    f"MCP tool {name!r} returned an error result",
                    provider_id=f"mcp:{self._config.server_id}",
                )
            if result.structured_content is not None:
                return _json_value(result.structured_content)
            return _json_value(result.model_dump(mode="json", by_alias=True))

    async def ping(self) -> bool:
        """Check connect/negotiation health without the deprecated MCP ping method."""

        try:
            async with self._client() as client:
                _ = client.protocol_version
            return True
        except Exception:
            return False


def build_mcp_provider(config: MCPServerConfig) -> MCPToolProvider:
    """Build the standard provider with the official optional MCP transport."""

    return MCPToolProvider(config, MCPPythonSDKClient(config))


def _json_object(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    converted = _json_value(dict(value))
    if not isinstance(converted, dict):  # pragma: no cover - defensive narrowing
        raise TypeError("expected JSON object")
    return converted


def _json_value(value: Any) -> JsonValue:
    """Normalize SDK/Pydantic output without exposing SDK objects to canonical APIs."""

    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("MCP JSON object keys must be strings")
            result[key] = _json_value(item)
        return result
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    raise TypeError(f"MCP SDK value is not JSON-compatible: {type(value).__name__}")
