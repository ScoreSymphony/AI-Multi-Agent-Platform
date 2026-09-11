"""Opt-in stateless MCP HTTP client for the 2026-07-28 protocol family.

The stable MCP adapter continues to use the official Python SDK. This module is a
small platform-owned compatibility adapter for the newer stateless wire contract while
upstream SDK/conformance support is still prerelease. It implements the existing
``MCPClient`` seam and does not introduce MCP-private types into canonical contracts.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .mcp import MCPClient, MCPServerConfig, MCPTool, MCPToolProvider

MCP_STATELESS_PROTOCOL_REVISION = "2026-07-28"
_PROTOCOL_HEADER = "MCP-Protocol-Version"
_META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
_META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
_META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
_UNSUPPORTED_PROTOCOL_VERSION = -32022


@dataclass(frozen=True, slots=True)
class _WireResponse:
    status: int
    payload: dict[str, Any]


class MCPStatelessHTTPClient(MCPClient):
    """Stateless HTTP MCP client with per-request metadata and version retry."""

    def __init__(
        self,
        config: MCPServerConfig,
        *,
        protocol_revision: str = MCP_STATELESS_PROTOCOL_REVISION,
        client_name: str = "ai-multi-agent-platform",
        client_version: str = "0.0.1",
    ) -> None:
        if config.endpoint is None:
            raise ValueError("stateless MCP client requires an HTTP endpoint")
        if protocol_revision != MCP_STATELESS_PROTOCOL_REVISION:
            raise ValueError(
                "stateless MCP client currently supports only protocol revision "
                f"{MCP_STATELESS_PROTOCOL_REVISION}"
            )
        if not client_name.strip() or not client_version.strip():
            raise ValueError("MCP client identity fields must not be blank")
        self._config = config
        self._protocol_revision = protocol_revision
        self._client_name = client_name
        self._client_version = client_version
        self._request_id = 0

    async def list_tools(self) -> tuple[MCPTool, ...]:
        result = await self._request("tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise self._invalid_response("tools/list result did not contain a tools array")

        converted: list[MCPTool] = []
        for item in tools:
            if not isinstance(item, Mapping):
                raise self._invalid_response("tools/list contained a non-object tool")
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                raise self._invalid_response("tools/list contained a tool without a name")
            description = item.get("description")
            input_schema = item.get("inputSchema", {})
            output_schema = item.get("outputSchema")
            converted.append(
                MCPTool(
                    name=name,
                    description=description if isinstance(description, str) else "",
                    input_schema=_json_object(input_schema, field="inputSchema"),
                    output_schema=(
                        _json_object(output_schema, field="outputSchema")
                        if output_schema is not None
                        else None
                    ),
                )
            )
        return tuple(converted)

    async def call_tool(self, name: str, arguments: dict[str, JsonValue]) -> JsonValue:
        result = await self._request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments,
            },
        )
        if result.get("isError") is True:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"MCP tool {name!r} returned an error result",
                provider_id=f"mcp:{self._config.server_id}",
            )
        if "structuredContent" in result:
            return _json_value(result["structuredContent"])
        return _json_value(result)

    async def ping(self) -> bool:
        try:
            await self._request("server/discover", {})
            return True
        except Exception:
            return False

    async def _request(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, Any]:
        response = await self._send(method, params)
        error = response.payload.get("error")
        if isinstance(error, Mapping) and error.get("code") == _UNSUPPORTED_PROTOCOL_VERSION:
            supported = _supported_versions(error)
            if self._protocol_revision not in supported:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "MCP server rejected the configured protocol revision without a common version",
                    provider_id=f"mcp:{self._config.server_id}",
                    details={
                        "requested_protocol_revision": self._protocol_revision,
                        "supported_protocol_revisions": list(supported),
                    },
                )
            response = await self._send(method, params)
            error = response.payload.get("error")

        if error is not None:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"MCP request {method!r} returned a JSON-RPC error",
                provider_id=f"mcp:{self._config.server_id}",
                details={"http_status": response.status, "error": _json_value(error)},
            )

        result = response.payload.get("result")
        if not isinstance(result, Mapping):
            raise self._invalid_response(f"MCP request {method!r} returned no result object")
        return dict(result)

    async def _send(
        self,
        method: str,
        params: dict[str, JsonValue],
    ) -> _WireResponse:
        self._request_id += 1
        request_id = self._request_id
        wire_params: dict[str, JsonValue] = dict(params)
        wire_params["_meta"] = {
            _META_PROTOCOL_VERSION: self._protocol_revision,
            _META_CLIENT_CAPABILITIES: {},
            _META_CLIENT_INFO: {
                "name": self._client_name,
                "version": self._client_version,
            },
        }
        body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": wire_params,
        }
        return await asyncio.to_thread(self._post_json, body)

    def _post_json(self, body: dict[str, JsonValue]) -> _WireResponse:
        assert self._config.endpoint is not None
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = Request(
            self._config.endpoint,
            data=encoded,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                _PROTOCOL_HEADER: self._protocol_revision,
            },
            method="POST",
        )
        timeout = self._config.read_timeout_seconds or 10.0
        try:
            with urlopen(request, timeout=timeout) as raw:  # noqa: S310 - configured MCP endpoint
                return _decode_response(raw.status, raw.read())
        except HTTPError as exc:
            return _decode_response(exc.code, exc.read())
        except (TimeoutError, URLError) as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "stateless MCP HTTP request failed",
                retryable=True,
                provider_id=f"mcp:{self._config.server_id}",
            ) from exc

    def _invalid_response(self, message: str) -> ContractError:
        return ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            message,
            provider_id=f"mcp:{self._config.server_id}",
        )


def build_mcp_stateless_provider(
    config: MCPServerConfig,
    *,
    protocol_revision: str = MCP_STATELESS_PROTOCOL_REVISION,
) -> MCPToolProvider:
    """Build an explicit stateless MCP provider without changing the stable SDK default."""

    return MCPToolProvider(
        config,
        MCPStatelessHTTPClient(config, protocol_revision=protocol_revision),
    )


def _decode_response(status: int, body: bytes) -> _WireResponse:
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "MCP server returned a non-JSON response",
            details={"http_status": status},
        ) from exc
    if not isinstance(decoded, dict):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "MCP server returned a non-object JSON-RPC response",
            details={"http_status": status},
        )
    return _WireResponse(status=status, payload=decoded)


def _supported_versions(error: Mapping[str, Any]) -> tuple[str, ...]:
    data = error.get("data")
    if not isinstance(data, Mapping):
        return ()
    supported = data.get("supported")
    if not isinstance(supported, list):
        return ()
    return tuple(item for item in supported if isinstance(item, str) and item)


def _json_object(value: object, *, field: str) -> dict[str, JsonValue]:
    converted = _json_value(value)
    if not isinstance(converted, dict):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            f"MCP {field} must be a JSON object",
        )
    return converted


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "MCP JSON object keys must be strings",
                )
            result[key] = _json_value(item)
        return result
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    raise ContractError(
        ErrorCode.INVALID_PROVIDER_RESPONSE,
        f"MCP value is not JSON-compatible: {type(value).__name__}",
    )
