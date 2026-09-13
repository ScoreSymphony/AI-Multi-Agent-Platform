"""MCP adapter behind the platform-owned capability/tool contracts."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Protocol, cast

from ai_multi_agent_platform.capabilities.provider import CapabilityToolProvider
from ai_multi_agent_platform.capabilities.types import (
    CapabilityRegistration,
    CapabilitySpec,
    SideEffectClassification,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    AdapterMetadata,
    Capability,
    CapabilityKind,
    HealthStatus,
    JsonValue,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)

from .mcp_tasks import (
    MCP_TASKS_PROTOCOL_REVISION,
    InMemoryMCPTaskBindingStore,
    MCPImmediateToolResult,
    MCPTaskBinding,
    MCPTaskBindingStore,
    MCPTaskClient,
    MCPTaskSnapshot,
    MCPTaskStarted,
    MCPTaskStatus,
    binding_adapter_metadata,
    binding_for_snapshot,
    mark_cancellation_requested,
    mark_cancellation_result,
    mark_input_requests_responded,
    observe_snapshot,
    validate_binding_for_invocation,
    validate_task_invocation_context,
)

type MCPTaskInputHandler = Callable[
    [ToolInvocation, MCPTaskSnapshot], Awaitable[dict[str, JsonValue] | None]
]


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    """Transport-neutral MCP server configuration.

    Exactly one transport target is configured: ``endpoint`` for Streamable HTTP or
    ``command`` for a local stdio subprocess. Environment values are transport input and
    deliberately never copied into adapter metadata. ``protocol_revision`` is an optional
    exact compatibility guard for deployments that make a revision-specific claim.

    ``enable_tasks`` is deliberately opt-in. When enabled, a transport that implements the
    optional SEP-2663 ``MCPTaskClient`` seam may negotiate asynchronous tools/call results.
    Ordinary MCP clients and servers continue through the synchronous path unchanged.
    """

    server_id: str
    endpoint: str | None = None
    command: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    read_timeout_seconds: float | None = None
    protocol_revision: str | None = None
    capability_id_overrides: dict[str, str] = field(default_factory=dict)
    priority: int = 0
    enable_tasks: bool = False
    task_default_poll_interval_ms: int = 1000

    def __post_init__(self) -> None:
        if not self.server_id.strip():
            raise ValueError("server_id must not be blank")
        has_endpoint = self.endpoint is not None
        has_command = bool(self.command)
        if has_endpoint == has_command:
            raise ValueError("MCP server requires exactly one of endpoint or command")
        if self.endpoint is not None and not self.endpoint.strip():
            raise ValueError("endpoint must not be blank")
        if self.command and not self.command[0].strip():
            raise ValueError("MCP command executable must not be blank")
        if self.cwd is not None and not self.cwd.strip():
            raise ValueError("cwd must not be blank")
        if self.read_timeout_seconds is not None and self.read_timeout_seconds <= 0:
            raise ValueError("read_timeout_seconds must be greater than zero")
        if self.protocol_revision is not None and not self.protocol_revision.strip():
            raise ValueError("protocol_revision must not be blank")
        if any(not key.strip() for key in self.environment):
            raise ValueError("MCP environment keys must not be blank")
        if self.task_default_poll_interval_ms < 0:
            raise ValueError("task_default_poll_interval_ms must not be negative")


@dataclass(frozen=True, slots=True)
class MCPTool:
    """Minimal MCP tool projection required by the platform adapter."""

    name: str
    description: str = ""
    input_schema: dict[str, JsonValue] = field(default_factory=dict)
    output_schema: dict[str, JsonValue] | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("MCP tool name must not be blank")


class MCPClient(Protocol):
    """Small transport seam implemented by a real MCP SDK/client or a test fake."""

    async def list_tools(self) -> tuple[MCPTool, ...]: ...

    async def call_tool(self, name: str, arguments: dict[str, JsonValue]) -> JsonValue: ...

    async def ping(self) -> bool: ...


class MCPToolProvider(CapabilityToolProvider):
    """Expose MCP tools as canonical platform capabilities.

    SEP-2663 tasks are an optional execution detail. The provider keeps the canonical invocation
    open while an external task is polled and returns only after the normal ToolResult is available.
    The external task therefore cannot become a second platform Task/Run lifecycle authority.
    """

    def __init__(
        self,
        config: MCPServerConfig,
        client: MCPClient,
        *,
        task_binding_store: MCPTaskBindingStore | None = None,
        task_input_handler: MCPTaskInputHandler | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._cached_tools: dict[str, MCPTool] = {}
        self._task_binding_store = task_binding_store or InMemoryMCPTaskBindingStore()
        self._task_input_handler = task_input_handler
        self._task_locks: dict[str, asyncio.Lock] = {}
        self._task_lock_users: dict[str, int] = {}
        self._active_task_bindings: dict[str, MCPTaskBinding] = {}

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=f"mcp:{self._config.server_id}",
            provider_type="mcp",
            supported_operations=("invoke", "discover", "health"),
            capabilities=tuple(
                Capability(
                    name=self._canonical_capability_id(tool.name),
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                )
                for tool in self._cached_tools.values()
            ),
            health=HealthStatus.UNKNOWN,
            available=True,
            adapter_metadata=(
                AdapterMetadata(
                    namespace="mcp",
                    values={"server_id": self._config.server_id},
                ),
            ),
        )

    async def health(self) -> HealthStatus:
        try:
            return HealthStatus.HEALTHY if await self._client.ping() else HealthStatus.UNAVAILABLE
        except Exception:
            return HealthStatus.UNAVAILABLE

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        try:
            tools = await self._client.list_tools()
        except Exception as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"MCP server {self._config.server_id!r} discovery failed",
                provider_id=self.descriptor.provider_id,
                adapter_metadata=self.descriptor.adapter_metadata,
            ) from exc

        self._cached_tools = {tool.name: tool for tool in tools}
        health = await self.health()
        registrations = []
        for tool in tools:
            registrations.append(
                CapabilityRegistration(
                    capability=CapabilitySpec(
                        capability_id=self._canonical_capability_id(tool.name),
                        name=tool.name,
                        description=tool.description,
                        version="1.0",
                        input_schema=tool.input_schema
                        or {
                            "$schema": "https://json-schema.org/draft/2020-12/schema",
                            "type": "object",
                        },
                        output_schema=tool.output_schema,
                        side_effects=SideEffectClassification.EXTERNAL,
                        health=health,
                        available=health is not HealthStatus.UNAVAILABLE,
                    ),
                    provider_id=self.descriptor.provider_id,
                    provider_tool_ref=tool.name,
                    priority=self._config.priority,
                    adapter_metadata=(
                        AdapterMetadata(
                            namespace="mcp",
                            values={
                                "server_id": self._config.server_id,
                                "tool_name": tool.name,
                            },
                        ),
                    ),
                )
            )
        return tuple(registrations)

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        if invocation.tool_ref not in self._cached_tools:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"MCP tool {invocation.tool_ref!r} is not available",
                provider_id=self.descriptor.provider_id,
            )
        try:
            if self._config.enable_tasks and isinstance(self._client, MCPTaskClient):
                return await self._invoke_task_aware(invocation, self._client)
            return await self._invoke_synchronous(invocation)
        except asyncio.CancelledError:
            raise
        except ContractError:
            raise
        except Exception as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"MCP tool {invocation.tool_ref!r} failed",
                provider_id=self.descriptor.provider_id,
                adapter_metadata=self._base_metadata(invocation.tool_ref),
            ) from exc

    async def _invoke_synchronous(self, invocation: ToolInvocation) -> ToolResult:
        output = await self._client.call_tool(
            invocation.tool_ref,
            invocation.arguments_json(),
        )
        return ToolResult(
            invocation_id=invocation.invocation_id,
            output=output,
            adapter_metadata=self._base_metadata(invocation.tool_ref),
        )

    async def _invoke_task_aware(
        self,
        invocation: ToolInvocation,
        client: MCPTaskClient,
    ) -> ToolResult:
        if client.task_protocol_revision != MCP_TASKS_PROTOCOL_REVISION:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "MCP Tasks transport revision does not match the pinned SEP-2663 profile",
                provider_id=self.descriptor.provider_id,
                details={
                    "expected_protocol_revision": MCP_TASKS_PROTOCOL_REVISION,
                    "actual_protocol_revision": client.task_protocol_revision,
                },
            )

        async with self._task_invocation_lock(invocation.invocation_id):
            binding = await self._task_binding_store.get(
                self.descriptor.provider_id,
                invocation.invocation_id,
            )
            if binding is not None:
                validate_binding_for_invocation(binding, invocation)
                self._active_task_bindings[invocation.invocation_id] = binding
                return await self._poll_bound_task(client, invocation, binding, initial=None)

            if not await client.supports_tasks():
                # A server without the extension stays on the ordinary MCP path only when this
                # canonical attempt has no durable external-task binding to reconcile.
                return await self._invoke_synchronous(invocation)

            # Validate all canonical authority/scoping inputs before the provider is allowed to
            # create external work. A task handle received before this check could otherwise become
            # an unbound side effect if binding construction later rejected the invocation context.
            validate_task_invocation_context(invocation, provider_id=self.descriptor.provider_id)

            # The in-process lock above only serializes one provider instance. The durable claim is
            # the cross-instance/process exactly-one dispatch boundary. It is intentionally retained
            # when delivery is ambiguous or when a task-capable call completes synchronously: the
            # same canonical attempt must not silently create external work again merely because no
            # async task handle exists to recover.
            claimed = await self._task_binding_store.claim_dispatch(
                self.descriptor.provider_id,
                invocation.invocation_id,
            )
            if not claimed:
                binding = await self._task_binding_store.get(
                    self.descriptor.provider_id,
                    invocation.invocation_id,
                )
                if binding is not None:
                    validate_binding_for_invocation(binding, invocation)
                    self._active_task_bindings[invocation.invocation_id] = binding
                    return await self._poll_bound_task(client, invocation, binding, initial=None)
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "MCP task-aware dispatch for this canonical invocation was already claimed; "
                    "refusing automatic redispatch because prior delivery may be in flight or "
                    "ambiguous",
                    provider_id=self.descriptor.provider_id,
                    details={
                        "mcp_task_dispatch_claimed": True,
                        "automatic_redispatch_blocked": True,
                    },
                )

            # Deliberately no transport-level retry here. If delivery becomes ambiguous before a
            # task handle is received, the durable dispatch claim remains consumed so this canonical
            # attempt fails closed rather than risking a duplicate external side effect. A platform
            # retry creates a new Run/attempt explicitly.
            outcome = await client.call_tool_with_tasks(
                invocation.tool_ref,
                invocation.arguments_json(),
            )
            if isinstance(outcome, MCPImmediateToolResult):
                return ToolResult(
                    invocation_id=invocation.invocation_id,
                    output=outcome.output,
                    adapter_metadata=(
                        *self._base_metadata(invocation.tool_ref),
                        AdapterMetadata(
                            namespace="mcp.tasks",
                            values={
                                "extension_id": "io.modelcontextprotocol/tasks",
                                "protocol_revision": MCP_TASKS_PROTOCOL_REVISION,
                                "execution_mode": "synchronous",
                            },
                        ),
                    ),
                )
            if not isinstance(outcome, MCPTaskStarted):  # pragma: no cover - protocol guard
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "MCP task-aware client returned an unsupported call outcome",
                    provider_id=self.descriptor.provider_id,
                )

            candidate = binding_for_snapshot(
                invocation,
                provider_id=self.descriptor.provider_id,
                server_id=self._config.server_id,
                protocol_revision=client.task_protocol_revision,
                snapshot=outcome.task,
            )
            try:
                binding = await self._task_binding_store.bind(candidate)
            except asyncio.CancelledError:
                # The durable store contract settles an already-started bind before cancellation
                # crosses the persistence boundary. At this point the exact external handle is
                # known and durably associated with this attempt, so cancellation must target it.
                await asyncio.shield(self._cancel_bound_task(client, invocation, candidate))
                raise
            validate_binding_for_invocation(binding, invocation)
            self._active_task_bindings[invocation.invocation_id] = binding
            return await self._poll_bound_task(client, invocation, binding, initial=outcome.task)

    @asynccontextmanager
    async def _task_invocation_lock(self, invocation_id: str) -> AsyncIterator[None]:
        """Serialize one canonical invocation without retaining historical lock entries."""

        lock = self._task_locks.setdefault(invocation_id, asyncio.Lock())
        self._task_lock_users[invocation_id] = self._task_lock_users.get(invocation_id, 0) + 1
        try:
            async with lock:
                yield
        finally:
            remaining = self._task_lock_users[invocation_id] - 1
            if remaining == 0:
                self._task_lock_users.pop(invocation_id, None)
                if self._task_locks.get(invocation_id) is lock:
                    self._task_locks.pop(invocation_id, None)
            else:
                self._task_lock_users[invocation_id] = remaining

    async def _poll_bound_task(
        self,
        client: MCPTaskClient,
        invocation: ToolInvocation,
        binding: MCPTaskBinding,
        *,
        initial: MCPTaskSnapshot | None,
    ) -> ToolResult:
        snapshot = initial
        current = binding
        preserve_failure_metadata = False
        try:
            while True:
                if snapshot is None:
                    try:
                        snapshot = await client.get_task(current.external_task_id)
                    except ContractError as exc:
                        if exc.code is ErrorCode.NOT_FOUND:
                            raise ContractError(
                                ErrorCode.NOT_FOUND,
                                "bound MCP task is missing or expired during recovery/polling",
                                provider_id=self.descriptor.provider_id,
                                details={
                                    "mcp_task_lost": True,
                                    "external_task_id_sha256": current.external_task_id_digest,
                                },
                                adapter_metadata=binding_adapter_metadata(current),
                            ) from exc
                        raise

                candidate = observe_snapshot(current, snapshot)
                current = await self._task_binding_store.save(candidate)
                self._active_task_bindings[invocation.invocation_id] = current

                # The binding store is the monotonic external-observation authority. If it retained
                # a newer observation, this poll is stale and must never drive terminal handling.
                if (
                    current.latest_observed_at != snapshot.last_updated_at
                    or current.latest_status is not snapshot.status
                ):
                    snapshot = None
                    continue

                if snapshot.status is MCPTaskStatus.COMPLETED:
                    output = self._normalize_completed_task_result(snapshot.result, current)
                    return ToolResult(
                        invocation_id=invocation.invocation_id,
                        output=output,
                        adapter_metadata=(
                            *self._base_metadata(invocation.tool_ref),
                            *binding_adapter_metadata(current),
                        ),
                    )

                if snapshot.status is MCPTaskStatus.FAILED:
                    raise ContractError(
                        ErrorCode.BACKEND_ERROR,
                        "external MCP task failed",
                        provider_id=self.descriptor.provider_id,
                        details={
                            "mcp_task_failed": True,
                            "external_task_id_sha256": current.external_task_id_digest,
                        },
                        adapter_metadata=binding_adapter_metadata(current),
                    )

                if snapshot.status is MCPTaskStatus.CANCELLED:
                    raise ContractError(
                        ErrorCode.CANCELLED,
                        "external MCP task reported cancellation",
                        provider_id=self.descriptor.provider_id,
                        details={
                            "external_task_id_sha256": current.external_task_id_digest,
                        },
                        adapter_metadata=binding_adapter_metadata(current),
                    )

                if snapshot.status is MCPTaskStatus.INPUT_REQUIRED:
                    requests = snapshot.input_requests or {}
                    outstanding = {
                        key: value
                        for key, value in requests.items()
                        if key not in current.responded_input_keys
                    }
                    if outstanding:
                        if self._task_input_handler is None:
                            await self._cancel_bound_task(client, invocation, current)
                            raise ContractError(
                                ErrorCode.UNSUPPORTED_CAPABILITY,
                                "MCP task requires interactive input but no governed input "
                                "handler is configured",
                                provider_id=self.descriptor.provider_id,
                                details={"mcp_task_input_required": True},
                                adapter_metadata=binding_adapter_metadata(
                                    self._active_task_bindings[invocation.invocation_id]
                                ),
                            )
                        handler_snapshot = replace(snapshot, input_requests=outstanding)
                        responses = await self._task_input_handler(invocation, handler_snapshot)
                        if not responses:
                            await self._cancel_bound_task(client, invocation, current)
                            raise ContractError(
                                ErrorCode.CANCELLED,
                                "MCP task input handler declined the external input request",
                                provider_id=self.descriptor.provider_id,
                                adapter_metadata=binding_adapter_metadata(
                                    self._active_task_bindings[invocation.invocation_id]
                                ),
                            )
                        unknown_keys = set(responses) - set(outstanding)
                        if unknown_keys:
                            await self._cancel_bound_task(client, invocation, current)
                            raise ContractError(
                                ErrorCode.CONTRACT_VIOLATION,
                                "MCP task input handler responded to unknown request keys",
                                provider_id=self.descriptor.provider_id,
                                details={
                                    "unknown_input_response_keys": cast(
                                        JsonValue, sorted(unknown_keys)
                                    )
                                },
                            )
                        await client.update_task(current.external_task_id, responses)
                        current = mark_input_requests_responded(current, tuple(responses))
                        current = await self._task_binding_store.save(current)
                        self._active_task_bindings[invocation.invocation_id] = current

                interval_ms = (
                    snapshot.poll_interval_ms
                    if snapshot.poll_interval_ms is not None
                    else self._config.task_default_poll_interval_ms
                )
                await asyncio.sleep(interval_ms / 1000)
                snapshot = None
        except asyncio.CancelledError:
            preserve_failure_metadata = True
            await asyncio.shield(self._cancel_bound_task(client, invocation, current))
            raise
        finally:
            if not preserve_failure_metadata:
                self._active_task_bindings.pop(invocation.invocation_id, None)

    async def _cancel_bound_task(
        self,
        client: MCPTaskClient,
        invocation: ToolInvocation,
        binding: MCPTaskBinding,
    ) -> None:
        current = mark_cancellation_requested(binding)
        current = await self._task_binding_store.save(current)
        self._active_task_bindings[invocation.invocation_id] = current
        try:
            await client.cancel_task(current.external_task_id)
        except ContractError as exc:
            current = mark_cancellation_result(
                current,
                acknowledged=False,
                error_code=exc.code.value,
            )
            current = await self._task_binding_store.save(current)
            self._active_task_bindings[invocation.invocation_id] = current
            return
        except Exception:
            current = mark_cancellation_result(
                current,
                acknowledged=False,
                error_code=ErrorCode.BACKEND_ERROR.value,
            )
            current = await self._task_binding_store.save(current)
            self._active_task_bindings[invocation.invocation_id] = current
            return
        current = mark_cancellation_result(current, acknowledged=True)
        current = await self._task_binding_store.save(current)
        self._active_task_bindings[invocation.invocation_id] = current

    def invocation_failure_metadata(
        self,
        invocation: ToolInvocation,
        *,
        error_code: str,
        duration_ms: float,
    ) -> tuple[AdapterMetadata, ...]:
        """Consume redacted task evidence for invoker-owned timeout/cancellation."""

        binding = self._active_task_bindings.pop(invocation.invocation_id, None)
        if binding is None:
            return ()
        return (
            *binding_adapter_metadata(binding),
            AdapterMetadata(
                namespace="mcp.tasks.invocation",
                values={
                    "canonical_error_code": error_code,
                    "provider_duration_ms": duration_ms,
                },
            ),
        )

    def _normalize_completed_task_result(
        self,
        result: JsonValue,
        binding: MCPTaskBinding,
    ) -> JsonValue:
        if not isinstance(result, dict):
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "completed MCP tools/call task returned a non-object final result",
                provider_id=self.descriptor.provider_id,
                adapter_metadata=binding_adapter_metadata(binding),
            )
        if result.get("isError") is True:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "completed MCP task contains a tool error result",
                provider_id=self.descriptor.provider_id,
                adapter_metadata=binding_adapter_metadata(binding),
            )
        if "structuredContent" in result:
            return result["structuredContent"]
        return result

    def _base_metadata(self, tool_ref: str) -> tuple[AdapterMetadata, ...]:
        return (
            AdapterMetadata(
                namespace="mcp",
                values={
                    "server_id": self._config.server_id,
                    "tool_name": tool_ref,
                },
            ),
        )

    def _canonical_capability_id(self, tool_name: str) -> str:
        override = self._config.capability_id_overrides.get(tool_name)
        if override is not None:
            if not override.strip():
                raise ValueError("MCP capability ID override must not be blank")
            return override
        slug = re.sub(r"[^a-z0-9]+", ".", tool_name.strip().lower()).strip(".")
        return f"tool.{slug or 'unnamed'}"
