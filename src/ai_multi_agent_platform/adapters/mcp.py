"""MCP adapter behind the platform-owned capability/tool contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

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


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    """Transport-neutral MCP server configuration.

    Exactly one transport target is configured: ``endpoint`` for Streamable HTTP or
    ``command`` for a local stdio subprocess. Environment values are transport input and
    deliberately never copied into adapter metadata.
    """

    server_id: str
    endpoint: str | None = None
    command: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    read_timeout_seconds: float | None = None
    capability_id_overrides: dict[str, str] = field(default_factory=dict)
    priority: int = 0

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
        if any(not key.strip() for key in self.environment):
            raise ValueError("MCP environment keys must not be blank")


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
    """Expose MCP tools as canonical platform capabilities."""

    def __init__(self, config: MCPServerConfig, client: MCPClient) -> None:
        self._config = config
        self._client = client
        self._cached_tools: dict[str, MCPTool] = {}

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
            output = await self._client.call_tool(
                invocation.tool_ref,
                invocation.arguments_json(),
            )
        except ContractError:
            raise
        except Exception as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"MCP tool {invocation.tool_ref!r} failed",
                provider_id=self.descriptor.provider_id,
                adapter_metadata=(
                    AdapterMetadata(
                        namespace="mcp",
                        values={
                            "server_id": self._config.server_id,
                            "tool_name": invocation.tool_ref,
                        },
                    ),
                ),
            ) from exc
        return ToolResult(
            invocation_id=invocation.invocation_id,
            output=output,
            adapter_metadata=(
                AdapterMetadata(
                    namespace="mcp",
                    values={
                        "server_id": self._config.server_id,
                        "tool_name": invocation.tool_ref,
                    },
                ),
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
