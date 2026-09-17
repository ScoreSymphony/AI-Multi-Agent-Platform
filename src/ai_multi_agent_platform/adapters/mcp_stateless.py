"""Opt-in stateless MCP HTTP client for the 2026-07-28 protocol family.

The stable MCP adapter continues to use the official Python SDK. This module is a
small platform-owned compatibility adapter for the newer stateless wire contract while
upstream SDK/conformance support is still prerelease. It implements the existing
``MCPClient`` seam and, when explicitly enabled by the provider, the finalized SEP-2663
Tasks extension without introducing MCP-private types into canonical contracts.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .mcp import MCPClient, MCPServerConfig, MCPTool, MCPToolProvider
from .mcp_tasks import (
    MCP_TASKS_EXTENSION_ID,
    MCP_TASKS_PROTOCOL_REVISION,
    MCPImmediateToolResult,
    MCPTaskCallResult,
    MCPTaskSnapshot,
    MCPTaskStarted,
    MCPTaskStatus,
)

MCP_STATELESS_PROTOCOL_REVISION = "2026-07-28"
_PROTOCOL_HEADER = "MCP-Protocol-Version"
_META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
_META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
_META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
_MCP_NAME_HEADER = "Mcp-Name"
_MCP_METHOD_HEADER = "Mcp-Method"
_MCP_PARAM_HEADER_PREFIX = "Mcp-Param-"
_X_MCP_HEADER = "x-mcp-header"
_UNSUPPORTED_PROTOCOL_VERSION = -32022
_MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
_HEADER_MISMATCH = -32020
_INVALID_PARAMS = -32602
_METHOD_NOT_FOUND = -32601
_TASK_METHODS = frozenset({"tasks/get", "tasks/update", "tasks/cancel"})
_NAME_PARAM_BY_METHOD = {
    "tools/call": "name",
    "resources/read": "uri",
    "prompts/get": "name",
    "tasks/get": "taskId",
    "tasks/update": "taskId",
    "tasks/cancel": "taskId",
}
_BASE64_SENTINEL_PREFIX = "=?base64?"
_BASE64_SENTINEL_SUFFIX = "?="
_HTTP_FIELD_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_HEADER_PARAMETER_TYPES = frozenset({"string", "integer", "boolean"})
_MAX_SAFE_INTEGER = (2**53) - 1
_MISSING = object()
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _WireResponse:
    status: int
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _ToolHeaderParameter:
    path: tuple[str, ...]
    header_name: str
    value_type: str


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
        self._tool_header_parameters: dict[str, tuple[_ToolHeaderParameter, ...]] = {}

    @property
    def task_protocol_revision(self) -> str:
        return MCP_TASKS_PROTOCOL_REVISION

    async def list_tools(self) -> tuple[MCPTool, ...]:
        result = await self._request("tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise self._invalid_response("tools/list result did not contain a tools array")

        converted: list[MCPTool] = []
        header_parameters: dict[str, tuple[_ToolHeaderParameter, ...]] = {}
        for item in tools:
            if not isinstance(item, Mapping):
                raise self._invalid_response("tools/list contained a non-object tool")
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                raise self._invalid_response("tools/list contained a tool without a name")
            description = item.get("description")
            input_schema = _json_object(item.get("inputSchema", {}), field="inputSchema")
            output_schema = item.get("outputSchema")
            try:
                tool_header_parameters = _extract_tool_header_parameters(input_schema)
            except ValueError as exc:
                # 2026-07-28 requires an invalid x-mcp-header annotation to reject only the
                # malformed tool definition, not otherwise valid tools from the same tools/list.
                _LOGGER.warning(
                    "excluding MCP tool %r because x-mcp-header metadata is invalid: %s",
                    name,
                    exc,
                )
                continue
            converted.append(
                MCPTool(
                    name=name,
                    description=description if isinstance(description, str) else "",
                    input_schema=input_schema,
                    output_schema=(
                        _json_object(output_schema, field="outputSchema")
                        if output_schema is not None
                        else None
                    ),
                )
            )
            header_parameters[name] = tool_header_parameters
        # Replace the cache only after the whole discovery response has been processed so removed
        # tools/annotations cannot survive a subsequent tools/list refresh.
        self._tool_header_parameters = header_parameters
        return tuple(converted)

    async def call_tool(self, name: str, arguments: dict[str, JsonValue]) -> JsonValue:
        result = await self._request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments,
            },
        )
        return self._normalize_immediate_tool_result(name, result)

    async def supports_tasks(self) -> bool:
        """Negotiate the finalized extension through server/discover capabilities."""

        try:
            result = await self._request("server/discover", {})
        except ContractError as exc:
            if exc.details.get("jsonrpc_error_code") == _METHOD_NOT_FOUND:
                return False
            raise
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, Mapping):
            return False
        extensions = capabilities.get("extensions")
        if not isinstance(extensions, Mapping):
            return False
        declaration = extensions.get(MCP_TASKS_EXTENSION_ID)
        return isinstance(declaration, Mapping)

    async def call_tool_with_tasks(
        self,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> MCPTaskCallResult:
        """Advertise SEP-2663 for this request and handle its polymorphic result shape."""

        result = await self._request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments,
            },
            client_extensions=(MCP_TASKS_EXTENSION_ID,),
        )
        result_type = result.get("resultType")
        if result_type == "task":
            # CreateTaskResult is ``Result & Task`` rather than ``DetailedTask``. Its seed state
            # can legally already be terminal or input_required without carrying result/error/
            # inputRequests. Resolve those non-working seed states through tasks/get before
            # exposing them to the provider's detailed lifecycle logic.
            try:
                task_id = _required_string(result, "taskId")
                seed_status = MCPTaskStatus(_required_string(result, "status"))
            except ValueError as exc:
                raise self._invalid_response(
                    "MCP CreateTaskResult does not match the pinned SEP-2663 Task shape"
                ) from exc
            if seed_status in {
                MCPTaskStatus.INPUT_REQUIRED,
                MCPTaskStatus.COMPLETED,
                MCPTaskStatus.FAILED,
            }:
                return MCPTaskStarted(await self.get_task(task_id))
            return MCPTaskStarted(_task_snapshot(result, provider_id=self._provider_id))
        if result_type == "input_required":
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "synchronous MCP multi-round-trip input is not handled by the Tasks adapter",
                provider_id=self._provider_id,
            )
        return MCPImmediateToolResult(self._normalize_immediate_tool_result(name, result))

    async def get_task(self, task_id: str) -> MCPTaskSnapshot:
        result = await self._request(
            "tasks/get",
            {"taskId": task_id},
            client_extensions=(MCP_TASKS_EXTENSION_ID,),
        )
        if result.get("resultType") != "complete":
            raise self._invalid_response("tasks/get resultType must be 'complete'")
        return _task_snapshot(result, provider_id=self._provider_id)

    async def update_task(
        self,
        task_id: str,
        input_responses: dict[str, JsonValue],
    ) -> None:
        if not input_responses:
            raise ValueError("MCP tasks/update requires at least one input response")
        result = await self._request(
            "tasks/update",
            {
                "taskId": task_id,
                "inputResponses": input_responses,
            },
            client_extensions=(MCP_TASKS_EXTENSION_ID,),
        )
        if result.get("resultType") != "complete":
            raise self._invalid_response("tasks/update resultType must be 'complete'")

    async def cancel_task(self, task_id: str) -> None:
        result = await self._request(
            "tasks/cancel",
            {"taskId": task_id},
            client_extensions=(MCP_TASKS_EXTENSION_ID,),
        )
        if result.get("resultType") != "complete":
            raise self._invalid_response("tasks/cancel resultType must be 'complete'")

    async def ping(self) -> bool:
        try:
            await self._request("server/discover", {})
            return True
        # error-boundary: allow-broad-catch=boundary reviewed owner containment boundary
        except Exception:
            return False

    @property
    def _provider_id(self) -> str:
        return f"mcp:{self._config.server_id}"

    def _normalize_immediate_tool_result(
        self,
        name: str,
        result: Mapping[str, Any],
    ) -> JsonValue:
        if result.get("isError") is True:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"MCP tool {name!r} returned an error result",
                provider_id=self._provider_id,
            )
        if "structuredContent" in result:
            return _json_value(result["structuredContent"])
        return _json_value(result)

    async def _request(
        self,
        method: str,
        params: dict[str, JsonValue],
        *,
        client_extensions: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        response = await self._send(method, params, client_extensions=client_extensions)
        error = response.payload.get("error")
        if isinstance(error, Mapping) and error.get("code") == _UNSUPPORTED_PROTOCOL_VERSION:
            supported = _supported_versions(error)
            if self._protocol_revision not in supported:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "MCP server rejected the configured protocol revision without a common version",
                    provider_id=self._provider_id,
                    details={
                        "requested_protocol_revision": self._protocol_revision,
                        "supported_protocol_revisions": list(supported),
                    },
                )
            response = await self._send(method, params, client_extensions=client_extensions)
            error = response.payload.get("error")

        if (
            isinstance(error, Mapping)
            and error.get("code") == _HEADER_MISMATCH
            and method == "tools/call"
        ):
            # The modern transport recommends refreshing tools/list because x-mcp-header metadata
            # can change independently of an already-cached tool definition. Retry exactly once
            # after that authoritative schema refresh.
            await self.list_tools()
            response = await self._send(method, params, client_extensions=client_extensions)
            error = response.payload.get("error")

        if error is not None:
            self._raise_jsonrpc_error(method, response.status, error)

        result = response.payload.get("result")
        if not isinstance(result, Mapping):
            raise self._invalid_response(f"MCP request {method!r} returned no result object")
        return dict(result)

    def _raise_jsonrpc_error(self, method: str, status: int, error: object) -> None:
        converted = _json_value(error)
        code: int | None = None
        if isinstance(error, Mapping):
            raw_code = error.get("code")
            if isinstance(raw_code, int) and not isinstance(raw_code, bool):
                code = raw_code
        details: dict[str, JsonValue] = {
            "http_status": status,
            "error": converted,
            "jsonrpc_method": method,
        }
        if code is not None:
            details["jsonrpc_error_code"] = code

        if method in _TASK_METHODS and code == _INVALID_PARAMS:
            details["mcp_task_lost"] = True
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "MCP task is unknown, expired or otherwise unavailable",
                provider_id=self._provider_id,
                details=details,
            )
        if method in _TASK_METHODS and code == _MISSING_REQUIRED_CLIENT_CAPABILITY:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "MCP server rejected a negotiated Tasks request as missing the "
                "extension capability",
                provider_id=self._provider_id,
                details=details,
            )
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            f"MCP request {method!r} returned a JSON-RPC error",
            provider_id=self._provider_id,
            details=details,
        )

    async def _send(
        self,
        method: str,
        params: dict[str, JsonValue],
        *,
        client_extensions: tuple[str, ...] = (),
    ) -> _WireResponse:
        self._request_id += 1
        request_id = self._request_id
        wire_params: dict[str, JsonValue] = dict(params)
        capabilities: dict[str, JsonValue] = {}
        if client_extensions:
            capabilities["extensions"] = {extension: {} for extension in client_extensions}
        wire_params["_meta"] = {
            _META_PROTOCOL_VERSION: self._protocol_revision,
            _META_CLIENT_CAPABILITIES: capabilities,
            _META_CLIENT_INFO: {
                "name": self._client_name,
                "version": self._client_version,
            },
        }
        body: dict[str, JsonValue] = {
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
            headers=self._request_headers(body),
            method="POST",
        )
        timeout = self._config.read_timeout_seconds or 10.0
        try:
            with urlopen(request, timeout=timeout) as raw:
                return _decode_response(
                    raw.status,
                    raw.read(),
                    content_type=raw.headers.get("Content-Type"),
                )
        except HTTPError as exc:
            return _decode_response(
                exc.code,
                exc.read(),
                content_type=exc.headers.get("Content-Type"),
            )
        except (TimeoutError, URLError) as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "stateless MCP HTTP request failed",
                retryable=True,
                provider_id=self._provider_id,
            ) from exc

    def _request_headers(self, body: Mapping[str, JsonValue]) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            _PROTOCOL_HEADER: self._protocol_revision,
        }
        method = body.get("method")
        if not isinstance(method, str) or not method.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "MCP request requires a non-blank JSON-RPC method",
                provider_id=self._provider_id,
            )
        # 2026-07-28 requires Mcp-Method on every Streamable HTTP request.
        headers[_MCP_METHOD_HEADER] = method

        name_field = _NAME_PARAM_BY_METHOD.get(method)
        if name_field is None:
            return headers
        params = body.get("params")
        if not isinstance(params, Mapping):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"MCP {method} requires request params for routing metadata",
                provider_id=self._provider_id,
            )
        name = params.get(name_field)
        if not isinstance(name, str) or not name.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"MCP {method} requires a non-blank {name_field}",
                provider_id=self._provider_id,
            )
        headers[_MCP_NAME_HEADER] = _encode_header_value(name)

        if method == "tools/call":
            arguments = params.get("arguments", {})
            if not isinstance(arguments, Mapping):
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "MCP tools/call arguments must be a JSON object",
                    provider_id=self._provider_id,
                )
            for parameter in self._tool_header_parameters.get(name, ()):
                value = _value_at_path(arguments, parameter.path)
                if value is _MISSING or value is None:
                    continue
                try:
                    text = _header_parameter_text(value, parameter.value_type)
                except ValueError as exc:
                    raise ContractError(
                        ErrorCode.INVALID_REQUEST,
                        "MCP tools/call argument does not match its x-mcp-header primitive type",
                        provider_id=self._provider_id,
                        details={
                            "tool_name": name,
                            "argument_path": ".".join(parameter.path),
                            "expected_type": parameter.value_type,
                        },
                    ) from exc
                headers[f"{_MCP_PARAM_HEADER_PREFIX}{parameter.header_name}"] = (
                    _encode_header_value(text)
                )
        return headers

    def _invalid_response(self, message: str) -> ContractError:
        return ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            message,
            provider_id=self._provider_id,
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


def _task_snapshot(result: Mapping[str, Any], *, provider_id: str) -> MCPTaskSnapshot:
    try:
        task_id = _required_string(result, "taskId")
        status = MCPTaskStatus(_required_string(result, "status"))
        created_at = _parse_timestamp(_required_string(result, "createdAt"), "createdAt")
        updated_at = _parse_timestamp(_required_string(result, "lastUpdatedAt"), "lastUpdatedAt")
        ttl_ms = _optional_non_negative_int(result.get("ttlMs"), "ttlMs", nullable=True)
        poll_interval_ms = _optional_non_negative_int(
            result.get("pollIntervalMs"),
            "pollIntervalMs",
            nullable=True,
        )
        status_message_value = result.get("statusMessage")
        status_message = status_message_value if isinstance(status_message_value, str) else None
        task_result: JsonValue = None
        task_error: JsonValue = None
        input_requests: dict[str, JsonValue] | None = None
        if status is MCPTaskStatus.COMPLETED:
            task_result = _json_object(result.get("result"), field="result")
        elif status is MCPTaskStatus.FAILED:
            task_error = _json_object(result.get("error"), field="error")
        elif status is MCPTaskStatus.INPUT_REQUIRED:
            input_requests = _json_object(result.get("inputRequests"), field="inputRequests")
        return MCPTaskSnapshot(
            task_id=task_id,
            status=status,
            created_at=created_at,
            last_updated_at=updated_at,
            ttl_ms=ttl_ms,
            poll_interval_ms=poll_interval_ms,
            status_message=status_message,
            result=task_result,
            error=task_error,
            input_requests=input_requests,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "MCP task response does not match the pinned SEP-2663 shape",
            provider_id=provider_id,
        ) from exc


def _required_string(value: Mapping[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return item


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _optional_non_negative_int(value: object, field: str, *, nullable: bool) -> int | None:
    if value is None and nullable:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer or null")
    return value


def _extract_tool_header_parameters(
    input_schema: Mapping[str, JsonValue],
) -> tuple[_ToolHeaderParameter, ...]:
    """Validate and collect every statically reachable 2026-07-28 x-mcp-header annotation."""

    collected: list[_ToolHeaderParameter] = []
    used_header_names: set[str] = set()

    def walk_property_schema(node: Mapping[str, object], path: tuple[str, ...]) -> None:
        annotation = node.get(_X_MCP_HEADER, _MISSING)
        if annotation is not _MISSING:
            if not path:
                raise ValueError("x-mcp-header must annotate a property, not the schema root")
            if not isinstance(annotation, str) or not annotation:
                raise ValueError("x-mcp-header must be a non-empty string")
            if _HTTP_FIELD_NAME.fullmatch(annotation) is None:
                raise ValueError("x-mcp-header must use HTTP field-name token syntax")
            folded = annotation.casefold()
            if folded in used_header_names:
                raise ValueError("x-mcp-header values must be case-insensitively unique")
            value_type = node.get("type")
            if not isinstance(value_type, str) or value_type not in _HEADER_PARAMETER_TYPES:
                raise ValueError(
                    "x-mcp-header may only annotate string, integer, or boolean properties"
                )
            used_header_names.add(folded)
            collected.append(
                _ToolHeaderParameter(
                    path=path,
                    header_name=annotation,
                    value_type=value_type,
                )
            )

        properties = node.get("properties")
        if isinstance(properties, Mapping):
            for property_name, property_schema in properties.items():
                if not isinstance(property_name, str):
                    raise ValueError("JSON Schema property names must be strings")
                if isinstance(property_schema, Mapping):
                    walk_property_schema(property_schema, (*path, property_name))
                else:
                    _reject_disallowed_header_annotation(property_schema)

        # Any annotation reachable through a keyword other than a direct `properties` chain is
        # invalid in the 2026-07-28 transport profile. Scan those branches solely to reject such
        # annotations; ordinary JSON Schema features otherwise remain untouched.
        for keyword, value in node.items():
            if keyword in {_X_MCP_HEADER, "properties"}:
                continue
            _reject_disallowed_header_annotation(value)

    root = cast_mapping(input_schema)
    if _X_MCP_HEADER in root:
        raise ValueError("x-mcp-header must annotate a property, not the schema root")
    properties = root.get("properties")
    if isinstance(properties, Mapping):
        for property_name, property_schema in properties.items():
            if not isinstance(property_name, str):
                raise ValueError("JSON Schema property names must be strings")
            if isinstance(property_schema, Mapping):
                walk_property_schema(property_schema, (property_name,))
            else:
                _reject_disallowed_header_annotation(property_schema)
    for keyword, value in root.items():
        if keyword == "properties":
            continue
        _reject_disallowed_header_annotation(value)
    return tuple(collected)


def cast_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, object]:
    """Narrow a JSON object for recursive schema inspection without altering its data."""

    return value


def _reject_disallowed_header_annotation(value: object) -> None:
    if isinstance(value, Mapping):
        if _X_MCP_HEADER in value:
            raise ValueError(
                "x-mcp-header must be statically reachable through properties-only schema paths"
            )
        for nested in value.values():
            _reject_disallowed_header_annotation(nested)
    elif isinstance(value, list | tuple):
        for nested in value:
            _reject_disallowed_header_annotation(nested)


def _value_at_path(arguments: Mapping[str, object], path: tuple[str, ...]) -> object:
    current: object = arguments
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _header_parameter_text(value: object, value_type: str) -> str:
    if value_type == "string":
        if not isinstance(value, str):
            raise ValueError("expected string")
        return value
    if value_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("expected integer")
        if value < -_MAX_SAFE_INTEGER or value > _MAX_SAFE_INTEGER:
            raise ValueError("integer is outside the JavaScript safe range")
        return str(value)
    if value_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("expected boolean")
        return "true" if value else "false"
    raise ValueError("unsupported x-mcp-header primitive type")


def _encode_header_value(value: str) -> str:
    """Encode mirrored MCP values using the 2026-07-28 Base64 sentinel rules."""

    matches_sentinel = value.startswith(_BASE64_SENTINEL_PREFIX) and value.endswith(
        _BASE64_SENTINEL_SUFFIX
    )
    safe_ascii = (
        bool(value)
        and value == value.strip(" \t")
        and not matches_sentinel
        and all(char in {" ", "\t"} or 0x21 <= ord(char) <= 0x7E for char in value)
    )
    if safe_ascii:
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"{_BASE64_SENTINEL_PREFIX}{encoded}{_BASE64_SENTINEL_SUFFIX}"


def _decode_response(
    status: int,
    body: bytes,
    *,
    content_type: str | None = None,
) -> _WireResponse:
    media_type = None if content_type is None else content_type.split(";", 1)[0].strip().lower()
    if media_type == "text/event-stream":
        return _decode_sse_response(status, body)
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


def _decode_sse_response(status: int, body: bytes) -> _WireResponse:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "MCP server returned a non-UTF-8 SSE response",
            details={"http_status": status},
        ) from exc

    final_response: dict[str, Any] | None = None
    data_lines: list[str] = []
    for line in (*text.splitlines(), ""):
        if line == "":
            if not data_lines:
                continue
            event_data = "\n".join(data_lines)
            data_lines = []
            try:
                decoded = json.loads(event_data)
            except json.JSONDecodeError as exc:
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "MCP SSE event contained invalid JSON",
                    details={"http_status": status},
                ) from exc
            if not isinstance(decoded, dict):
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "MCP SSE event contained a non-object JSON-RPC message",
                    details={"http_status": status},
                )
            if "id" in decoded and ("result" in decoded or "error" in decoded):
                if final_response is not None:
                    raise ContractError(
                        ErrorCode.INVALID_PROVIDER_RESPONSE,
                        "MCP SSE response contained multiple final JSON-RPC responses",
                        details={"http_status": status},
                    )
                final_response = decoded
                continue
            if decoded.get("jsonrpc") == "2.0" and isinstance(decoded.get("method"), str):
                # Request-scoped progress/log notifications are evidence only; the stateless
                # compatibility client waits for the final response and leaves canonical lifecycle
                # authority to the platform provider/invoker.
                continue
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "MCP SSE stream contained an unsupported JSON-RPC message",
                details={"http_status": status},
            )
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and field == "data":
            data_lines.append(value[1:] if value.startswith(" ") else value)

    if final_response is None:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "MCP SSE response ended without a final JSON-RPC response",
            details={"http_status": status},
        )
    return _WireResponse(status=status, payload=final_response)


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
