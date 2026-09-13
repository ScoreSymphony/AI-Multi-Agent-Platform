"""Opt-in stateless MCP HTTP client for the 2026-07-28 protocol family.

The stable MCP adapter continues to use the official Python SDK. This module is a
small platform-owned compatibility adapter for the newer stateless wire contract while
upstream SDK/conformance support is still prerelease. It implements the existing
``MCPClient`` seam and does not introduce MCP-private types into canonical contracts.

SEP-2663 Tasks are negotiated per request and transparently driven to a final tool result.
External task handles are persisted only as adapter evidence bound to the exact canonical
ToolInvocation attempt.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue, ToolInvocation

from .mcp import MCPClient, MCPServerConfig, MCPTool, MCPToolProvider
from .mcp_tasks import (
    MCP_TASK_CANCELLED,
    MCP_TASK_COMPLETED,
    MCP_TASK_FAILED,
    MCP_TASK_INPUT_REQUIRED,
    MCP_TASK_KNOWN_STATUSES,
    MCP_TASK_WORKING,
    MCP_TASKS_EXTENSION_ID,
    MCP_TASKS_PROTOCOL_REVISION,
    InMemoryMCPTaskBindingRepository,
    MCPInvocationOutcome,
    MCPTaskBinding,
    MCPTaskBindingRepository,
    MCPTaskObserver,
    NullMCPTaskObserver,
    binding_for_invocation,
    cancellation_requested,
    cancellation_result,
    observed_binding,
    task_adapter_metadata,
    validate_binding_scope,
)

MCP_STATELESS_PROTOCOL_REVISION = MCP_TASKS_PROTOCOL_REVISION
_PROTOCOL_HEADER = "MCP-Protocol-Version"
_META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
_META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
_META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
_UNSUPPORTED_PROTOCOL_VERSION = -32022

type MCPTaskInputHandler = Callable[
    [ToolInvocation, dict[str, JsonValue]],
    Awaitable[dict[str, JsonValue]],
]


@dataclass(frozen=True, slots=True)
class _WireResponse:
    status: int
    payload: dict[str, Any]


class MCPStatelessHTTPClient(MCPClient):
    """Stateless HTTP MCP client with per-request metadata, Tasks and version retry."""

    def __init__(
        self,
        config: MCPServerConfig,
        *,
        protocol_revision: str = MCP_STATELESS_PROTOCOL_REVISION,
        client_name: str = "ai-multi-agent-platform",
        client_version: str = "0.0.1",
        task_bindings: MCPTaskBindingRepository | None = None,
        task_observer: MCPTaskObserver | None = None,
        task_input_handler: MCPTaskInputHandler | None = None,
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
        self._task_bindings = task_bindings or InMemoryMCPTaskBindingRepository()
        self._task_observer = task_observer or NullMCPTaskObserver()
        self._task_input_handler = task_input_handler
        self._tasks_supported: bool | None = None
        self._task_dispatch_lock = asyncio.Lock()
        self._failure_metadata: dict[str, tuple[AdapterMetadata, ...]] = {}

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
        """Use the ordinary synchronous MCP path without declaring Tasks support."""

        result = await self._request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments,
            },
        )
        if result.get("resultType") == "task":
            raise self._invalid_response(
                "MCP server returned a task although the client did not declare Tasks support"
            )
        return self._normalize_tool_result(name, result)

    async def call_tool_for_invocation(
        self,
        invocation: ToolInvocation,
    ) -> MCPInvocationOutcome:
        """Invoke one tool while keeping external task state bound to this canonical attempt."""

        try:
            binding = await self._task_bindings.get(
                self._config.server_id,
                invocation.invocation_id,
            )
            if binding is not None:
                validate_binding_scope(binding, invocation)
                return await self._drive_bound_task(invocation, binding)

            if not await self._server_supports_tasks():
                return MCPInvocationOutcome(
                    output=await self.call_tool(
                        invocation.tool_ref,
                        invocation.arguments_json(),
                    )
                )

            initial_poll_delay = 0.0
            async with self._task_dispatch_lock:
                # Re-check after acquiring the dispatch lock so transport/concurrent retries
                # cannot create a second external task for one canonical invocation attempt.
                binding = await self._task_bindings.get(
                    self._config.server_id,
                    invocation.invocation_id,
                )
                if binding is None:
                    result = await self._request(
                        "tools/call",
                        {
                            "name": invocation.tool_ref,
                            "arguments": invocation.arguments_json(),
                        },
                        client_extensions=(MCP_TASKS_EXTENSION_ID,),
                    )
                    if result.get("resultType") != "task":
                        return MCPInvocationOutcome(
                            output=self._normalize_tool_result(invocation.tool_ref, result)
                        )
                    binding = await self._bind_created_task(invocation, result)
                    initial_poll_delay = _poll_delay_seconds(result)
                else:
                    validate_binding_scope(binding, invocation)
            if initial_poll_delay:
                await asyncio.sleep(initial_poll_delay)
            return await self._drive_bound_task(invocation, binding)
        except asyncio.CancelledError:
            binding = await self._task_bindings.get(
                self._config.server_id,
                invocation.invocation_id,
            )
            if binding is not None:
                validate_binding_scope(binding, invocation)
                await self._request_task_cancellation(binding, suppress_errors=True)
            raise

    def invocation_failure_metadata(
        self,
        invocation: ToolInvocation,
    ) -> tuple[AdapterMetadata, ...]:
        """Return already-captured evidence without doing synchronous persistence I/O."""

        return self._failure_metadata.get(invocation.invocation_id, ())

    async def cancel_invocation_task(
        self,
        invocation: ToolInvocation,
    ) -> tuple[AdapterMetadata, ...]:
        """Best-effort provider cancellation scoped to one already-authorized invocation."""

        binding = await self._require_bound_invocation(invocation)
        binding = await self._request_task_cancellation(binding, suppress_errors=False)
        return task_adapter_metadata(binding)

    async def update_invocation_task(
        self,
        invocation: ToolInvocation,
        input_responses: dict[str, JsonValue],
    ) -> tuple[AdapterMetadata, ...]:
        """Satisfy SEP-2663 input requests without exposing arbitrary task-ID control."""

        binding = await self._require_bound_invocation(invocation)
        await self._request(
            "tasks/update",
            {
                "taskId": binding.external_task_id,
                "inputResponses": input_responses,
            },
            client_extensions=(MCP_TASKS_EXTENSION_ID,),
        )
        return task_adapter_metadata(binding)

    async def ping(self) -> bool:
        try:
            await self._request("server/discover", {})
            return True
        except Exception:
            return False

    async def _server_supports_tasks(self) -> bool:
        if self._tasks_supported is not None:
            return self._tasks_supported
        try:
            result = await self._request(
                "server/discover",
                {},
                client_extensions=(MCP_TASKS_EXTENSION_ID,),
            )
        except ContractError as exc:
            if exc.code is ErrorCode.BACKEND_ERROR:
                # A server without extension discovery remains usable through the ordinary
                # synchronous tool path. Transport/protocol failures are not hidden here.
                self._tasks_supported = False
                return False
            raise

        capabilities = result.get("capabilities")
        extensions: object = None
        if isinstance(capabilities, Mapping):
            extensions = capabilities.get("extensions")
        self._tasks_supported = (
            isinstance(extensions, Mapping) and MCP_TASKS_EXTENSION_ID in extensions
        )
        return self._tasks_supported

    async def _bind_created_task(
        self,
        invocation: ToolInvocation,
        result: dict[str, Any],
    ) -> MCPTaskBinding:
        external_task_id = result.get("taskId")
        status = result.get("status")
        if not isinstance(external_task_id, str) or not external_task_id.strip():
            raise self._invalid_response("CreateTaskResult did not contain a taskId")
        if not isinstance(status, str) or status not in MCP_TASK_KNOWN_STATUSES:
            raise self._invalid_response("CreateTaskResult contained an unknown task status")

        created_at = result.get("createdAt")
        binding = binding_for_invocation(
            invocation,
            server_id=self._config.server_id,
            external_task_id=external_task_id,
            external_status=status,
            external_created_at=created_at if isinstance(created_at, str) else None,
            protocol_revision=self._protocol_revision,
        )
        binding = await self._task_bindings.put_if_absent(binding)
        validate_binding_scope(binding, invocation)
        await self._observe(binding)
        return binding

    async def _drive_bound_task(
        self,
        invocation: ToolInvocation,
        binding: MCPTaskBinding,
    ) -> MCPInvocationOutcome:
        validate_binding_scope(binding, invocation)
        while True:
            task = await self._get_task(binding)
            status = task.get("status")
            if not isinstance(status, str):
                raise self._invalid_response("tasks/get did not contain a task status")
            binding = observed_binding(binding, status)
            await self._task_bindings.update(binding)
            await self._observe(binding)

            if status == MCP_TASK_WORKING:
                await asyncio.sleep(_poll_delay_seconds(task))
                continue

            if status == MCP_TASK_INPUT_REQUIRED:
                input_requests = task.get("inputRequests")
                if not isinstance(input_requests, Mapping):
                    raise self._invalid_response(
                        "input_required task did not contain inputRequests"
                    )
                if self._task_input_handler is None:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "MCP task requires additional input before it can continue",
                        provider_id=f"mcp:{self._config.server_id}",
                        retryable=True,
                        details={"mcp_task_input_required": True},
                        adapter_metadata=task_adapter_metadata(binding),
                    )
                converted = _json_object(input_requests, field="inputRequests")
                responses = await self._task_input_handler(invocation, converted)
                await self.update_invocation_task(invocation, responses)
                await asyncio.sleep(_poll_delay_seconds(task))
                continue

            if status == MCP_TASK_COMPLETED:
                result = task.get("result")
                if not isinstance(result, Mapping):
                    raise self._invalid_response(
                        "completed MCP task did not contain its original result"
                    )
                output = self._normalize_tool_result(
                    invocation.tool_ref,
                    dict(result),
                )
                metadata = task_adapter_metadata(binding)
                self._failure_metadata[invocation.invocation_id] = metadata
                return MCPInvocationOutcome(output=output, adapter_metadata=metadata)

            if status == MCP_TASK_CANCELLED:
                metadata = task_adapter_metadata(binding)
                self._failure_metadata[invocation.invocation_id] = metadata
                raise ContractError(
                    ErrorCode.CANCELLED,
                    "MCP task reported cancellation",
                    provider_id=f"mcp:{self._config.server_id}",
                    retryable=True,
                    details={"external_task_cancelled": True},
                    adapter_metadata=metadata,
                )

            if status == MCP_TASK_FAILED:
                error = task.get("error")
                error_code: JsonValue = None
                if isinstance(error, Mapping):
                    raw_code = error.get("code")
                    if isinstance(raw_code, str | int | float | bool) or raw_code is None:
                        error_code = raw_code
                metadata = task_adapter_metadata(binding)
                self._failure_metadata[invocation.invocation_id] = metadata
                raise ContractError(
                    ErrorCode.BACKEND_ERROR,
                    "MCP task reported a protocol-level execution failure",
                    provider_id=f"mcp:{self._config.server_id}",
                    details={
                        "external_task_failed": True,
                        "external_error_code": error_code,
                    },
                    adapter_metadata=metadata,
                )

            # observed_binding already fails closed for future/unknown statuses.
            raise self._invalid_response("MCP task status was not handled")

    async def _get_task(self, binding: MCPTaskBinding) -> dict[str, Any]:
        try:
            return await self._request(
                "tasks/get",
                {"taskId": binding.external_task_id},
                client_extensions=(MCP_TASKS_EXTENSION_ID,),
            )
        except ContractError as exc:
            metadata = task_adapter_metadata(binding)
            self._failure_metadata[binding.invocation_id] = metadata
            if exc.code is ErrorCode.BACKEND_ERROR and "error" in exc.details:
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    "MCP external task state is no longer available from the server",
                    retryable=True,
                    provider_id=f"mcp:{binding.server_id}",
                    details={"external_task_state_unavailable": True},
                    adapter_metadata=metadata,
                ) from exc
            raise

    async def _require_bound_invocation(
        self,
        invocation: ToolInvocation,
    ) -> MCPTaskBinding:
        binding = await self._task_bindings.get(
            self._config.server_id,
            invocation.invocation_id,
        )
        if binding is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "no MCP external task is bound to this canonical invocation",
                provider_id=f"mcp:{self._config.server_id}",
            )
        validate_binding_scope(binding, invocation)
        return binding

    async def _request_task_cancellation(
        self,
        binding: MCPTaskBinding,
        *,
        suppress_errors: bool,
    ) -> MCPTaskBinding:
        if binding.latest_status in {MCP_TASK_COMPLETED, MCP_TASK_CANCELLED, MCP_TASK_FAILED}:
            return binding

        binding = cancellation_requested(binding)
        await self._task_bindings.update(binding)
        await self._observe(binding)
        try:
            await self._request(
                "tasks/cancel",
                {"taskId": binding.external_task_id},
                client_extensions=(MCP_TASKS_EXTENSION_ID,),
            )
        except ContractError as exc:
            outcome = (
                "unavailable"
                if exc.code in {ErrorCode.UNAVAILABLE, ErrorCode.TIMEOUT}
                else f"refused:{exc.code.value}"
            )
            binding = cancellation_result(binding, outcome)
            await self._task_bindings.update(binding)
            await self._observe(binding)
            self._failure_metadata[binding.invocation_id] = task_adapter_metadata(binding)
            if suppress_errors:
                return binding
            raise

        binding = cancellation_result(binding, "acknowledged")
        await self._task_bindings.update(binding)
        await self._observe(binding)
        self._failure_metadata[binding.invocation_id] = task_adapter_metadata(binding)
        return binding

    async def _observe(self, binding: MCPTaskBinding) -> None:
        self._failure_metadata[binding.invocation_id] = task_adapter_metadata(binding)
        await self._task_observer.record_task_observation(binding)

    def _normalize_tool_result(self, name: str, result: dict[str, Any]) -> JsonValue:
        if result.get("isError") is True:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"MCP tool {name!r} returned an error result",
                provider_id=f"mcp:{self._config.server_id}",
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
                    provider_id=f"mcp:{self._config.server_id}",
                    details={
                        "requested_protocol_revision": self._protocol_revision,
                        "supported_protocol_revisions": list(supported),
                    },
                )
            response = await self._send(method, params, client_extensions=client_extensions)
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
        *,
        client_extensions: tuple[str, ...] = (),
    ) -> _WireResponse:
        self._request_id += 1
        request_id = self._request_id
        client_capabilities: dict[str, JsonValue] = {}
        if client_extensions:
            client_capabilities["extensions"] = {extension: {} for extension in client_extensions}
        wire_params: dict[str, JsonValue] = dict(params)
        wire_params["_meta"] = {
            _META_PROTOCOL_VERSION: self._protocol_revision,
            _META_CLIENT_CAPABILITIES: client_capabilities,
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
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                _PROTOCOL_HEADER: self._protocol_revision,
            },
            method="POST",
        )
        timeout = self._config.read_timeout_seconds or 10.0
        try:
            with urlopen(request, timeout=timeout) as raw:
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
    task_bindings: MCPTaskBindingRepository | None = None,
    task_observer: MCPTaskObserver | None = None,
    task_input_handler: MCPTaskInputHandler | None = None,
) -> MCPToolProvider:
    """Build an explicit stateless MCP provider without changing the stable SDK default."""

    return MCPToolProvider(
        config,
        MCPStatelessHTTPClient(
            config,
            protocol_revision=protocol_revision,
            task_bindings=task_bindings,
            task_observer=task_observer,
            task_input_handler=task_input_handler,
        ),
    )


def _poll_delay_seconds(task: Mapping[str, Any]) -> float:
    value = task.get("pollIntervalMs", 0)
    if value is None:
        return 0.0
    if not isinstance(value, int | float) or isinstance(value, bool) or value < 0:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "MCP task pollIntervalMs must be a non-negative number",
        )
    return float(value) / 1000.0


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
