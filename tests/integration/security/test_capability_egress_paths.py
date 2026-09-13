from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig, MCPTool, MCPToolProvider
from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilitySpec,
    InvocationTrace,
)
from ai_multi_agent_platform.capabilities.provider import CapabilityToolProvider
from ai_multi_agent_platform.contracts import (
    Capability,
    CapabilityKind,
    ContractError,
    DataClassification,
    ErrorCode,
    HealthStatus,
    JsonValue,
    OperationContext,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.domain import new_id


class _BrowserNetworkProvider(CapabilityToolProvider):
    def __init__(self) -> None:
        self.calls = 0
        self._spec = CapabilitySpec(
            capability_id="browser.navigate",
            name="Navigate",
            input_schema={"type": "object"},
            tags=("browser", "web", "read"),
            required_permissions=("browser.network.read",),
            health=HealthStatus.HEALTHY,
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="browser-test-provider",
            provider_type="browser",
            capabilities=(
                Capability(
                    name=self._spec.capability_id,
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                ),
            ),
            health=HealthStatus.HEALTHY,
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        return (
            CapabilityRegistration(
                capability=self._spec,
                provider_id=self.descriptor.provider_id,
                provider_tool_ref="browser.test.navigate",
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        self.calls += 1
        return ToolResult(invocation_id=invocation.invocation_id, output={"ok": True})


class _MCPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, JsonValue]]] = []

    async def list_tools(self) -> tuple[MCPTool, ...]:
        return (
            MCPTool(
                name="lookup",
                description="Lookup",
                input_schema={"type": "object"},
            ),
        )

    async def call_tool(self, name: str, arguments: dict[str, JsonValue]) -> JsonValue:
        self.calls.append((name, arguments))
        return {"ok": True}

    async def ping(self) -> bool:
        return True


def _request(capability_id: str, arguments: dict[str, JsonValue]) -> CapabilityInvocation:
    project_id = new_id("project")
    context = OperationContext(
        correlation_id=f"corr-{capability_id}-591",
        owner_type="user",
        owner_id="user-591",
        project_id=project_id,
    )
    return CapabilityInvocation(
        invocation_id=f"invoke-{capability_id}-591",
        capability_id=capability_id,
        arguments=arguments,
        context=context,
        trace=InvocationTrace(
            correlation_id=context.correlation_id,
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=project_id,
        ),
        granted_permissions=frozenset(
            {"browser.network.read"} if capability_id == "browser.navigate" else ()
        ),
    )


def _secret_classification(
    request: CapabilityInvocation,
    capability: CapabilitySpec,
) -> DataClassification:
    del request, capability
    return DataClassification.SECRET


def test_browser_network_read_is_blocked_before_provider_execution_for_secret_data() -> None:
    async def scenario() -> None:
        provider = _BrowserNetworkProvider()
        registry = CapabilityRegistry()
        await registry.register_provider(provider)

        with pytest.raises(ContractError) as captured:
            await CapabilityInvoker(
                registry,
                classification_resolver=_secret_classification,
            ).invoke(_request("browser.navigate", {"url": "https://example.test"}))

        assert captured.value.code is ErrorCode.FORBIDDEN
        assert captured.value.details["target_posture"] == "external"
        assert captured.value.details["classification"] == "secret"
        assert provider.calls == 0

    asyncio.run(scenario())


def test_mcp_tool_is_blocked_by_same_egress_gate_for_secret_data() -> None:
    async def scenario() -> None:
        client = _MCPClient()
        provider = MCPToolProvider(
            MCPServerConfig(
                server_id="egress-test-server",
                endpoint="http://127.0.0.1:9999",
                capability_id_overrides={"lookup": "mcp.lookup"},
            ),
            client,
        )
        registry = CapabilityRegistry()
        await registry.register_provider(provider)

        with pytest.raises(ContractError) as captured:
            await CapabilityInvoker(
                registry,
                classification_resolver=_secret_classification,
            ).invoke(_request("mcp.lookup", {"query": "protected"}))

        assert captured.value.code is ErrorCode.FORBIDDEN
        assert captured.value.details["target_posture"] == "external"
        assert captured.value.details["classification"] == "secret"
        assert client.calls == []

    asyncio.run(scenario())
