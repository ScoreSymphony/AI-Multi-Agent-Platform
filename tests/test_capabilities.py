from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilitySpec,
    InvocationRecord,
    InvocationTrace,
    MCPServerConfig,
    MCPTool,
    MCPToolProvider,
    NativeEchoProvider,
    PolicyDecision,
)
from ai_multi_agent_platform.capabilities.provider import CapabilityToolProvider
from ai_multi_agent_platform.contracts.domain_mapping import map_tool_invocation_to_domain
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    HealthStatus,
    JsonValue,
    OperationContext,
    OperationControl,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.domain import OwnerRef, ToolInvocation as DomainToolInvocation, new_id


def _request(
    capability_id: str = "tool.echo",
    *,
    arguments: dict[str, JsonValue] | None = None,
    version: str | None = None,
    permissions: frozenset[str] = frozenset(),
    timeout: float | None = None,
) -> CapabilityInvocation:
    project_id = new_id("project")
    context = OperationContext(
        correlation_id="corr-1",
        owner_type="user",
        owner_id="user-1",
        project_id=project_id,
        control=OperationControl(timeout_seconds=timeout),
    )
    return CapabilityInvocation(
        invocation_id="invoke-1",
        capability_id=capability_id,
        version=version,
        arguments={"message": "hello"} if arguments is None else arguments,
        context=context,
        trace=InvocationTrace(
            correlation_id="corr-1",
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=project_id,
        ),
        granted_permissions=permissions,
    )


async def _governance_binding(
    request: CapabilityInvocation,
    registration: CapabilityRegistration,
    provider_invocation: ToolInvocation,
) -> DomainToolInvocation:
    return map_tool_invocation_to_domain(
        provider_invocation,
        canonical_tool_id=new_id("tool"),
        owner_ref=OwnerRef(type="user", id="user-1"),
        canonical_project_id=request.trace.project_id,
    )


class RecordingObserver:
    def __init__(self) -> None:
        self.records: list[InvocationRecord] = []

    async def record(self, record: InvocationRecord) -> None:
        self.records.append(record)


class StaticProvider(CapabilityToolProvider):
    def __init__(
        self,
        provider_id: str,
        spec: CapabilitySpec,
        *,
        priority: int = 0,
        delay: float = 0.0,
        fail: bool = False,
        cancel: bool = False,
    ) -> None:
        self._provider_id = provider_id
        self._spec = spec
        self._priority = priority
        self._delay = delay
        self._fail = fail
        self._cancel = cancel
        self.calls = 0

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self._provider_id,
            provider_type="test",
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
                provider_id=self._provider_id,
                provider_tool_ref="test.tool",
                priority=self._priority,
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._cancel:
            raise asyncio.CancelledError
        if self._fail:
            raise RuntimeError("backend exploded")
        return ToolResult(invocation_id=invocation.invocation_id, output={"ok": True})


class FakeMCPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, JsonValue]]] = []

    async def list_tools(self) -> tuple[MCPTool, ...]:
        return (
            MCPTool(
                name="lookup",
                description="Lookup a value",
                input_schema={
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
        )

    async def call_tool(self, name: str, arguments: dict[str, JsonValue]) -> JsonValue:
        self.calls.append((name, arguments))
        return {"source": name, "query": arguments["query"]}

    async def ping(self) -> bool:
        return True


def test_native_tool_success_and_trace_preservation() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        await registry.register_provider(NativeEchoProvider())
        observer = RecordingObserver()
        result = await CapabilityInvoker(registry, observer=observer).invoke(_request())

        assert result.output == {"message": "hello"}
        assert result.provider_id == "native.reference"
        assert [record.status.value for record in observer.records] == ["running", "succeeded"]
        assert observer.records[-1].trace.task_id.startswith("task_")
        assert observer.records[-1].trace.run_id.startswith("run_")
        assert observer.records[-1].trace.agent_id.startswith("agent_")

    asyncio.run(scenario())


def test_invalid_input_is_rejected_before_provider_execution() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        await registry.register_provider(NativeEchoProvider())
        with pytest.raises(ContractError) as caught:
            await CapabilityInvoker(registry).invoke(_request(arguments={"message": 42}))
        assert caught.value.code is ErrorCode.INVALID_REQUEST

    asyncio.run(scenario())


def test_mcp_tool_uses_same_canonical_invocation_path() -> None:
    async def scenario() -> None:
        client = FakeMCPClient()
        provider = MCPToolProvider(
            MCPServerConfig(
                server_id="test-server",
                endpoint="http://127.0.0.1:9999",
                capability_id_overrides={"lookup": "tool.lookup"},
                priority=10,
            ),
            client,
        )
        registry = CapabilityRegistry()
        await registry.register_provider(provider)

        request = _request("tool.lookup", arguments={"query": "abc"})
        result = await CapabilityInvoker(registry).invoke(request)

        assert result.output == {"source": "lookup", "query": "abc"}
        assert client.calls == [("lookup", {"query": "abc"})]
        assert result.capability_id == "tool.lookup"

    asyncio.run(scenario())


def test_unavailable_provider_fails_canonically() -> None:
    async def scenario() -> None:
        spec = CapabilitySpec(
            capability_id="tool.offline",
            name="Offline",
            input_schema={"type": "object"},
            health=HealthStatus.UNAVAILABLE,
            available=False,
        )
        registry = CapabilityRegistry()
        await registry.register_provider(StaticProvider("offline", spec))
        with pytest.raises(ContractError) as caught:
            registry.resolve("tool.offline")
        assert caught.value.code is ErrorCode.UNAVAILABLE

    asyncio.run(scenario())


def test_timeout_is_mapped_to_canonical_error() -> None:
    async def scenario() -> None:
        spec = CapabilitySpec(
            capability_id="tool.slow",
            name="Slow",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            timeout_seconds=0.001,
            health=HealthStatus.HEALTHY,
        )
        registry = CapabilityRegistry()
        await registry.register_provider(StaticProvider("slow", spec, delay=0.05))
        with pytest.raises(ContractError) as caught:
            await CapabilityInvoker(registry).invoke(
                _request("tool.slow", arguments={"message": "x"})
            )
        assert caught.value.code is ErrorCode.TIMEOUT

    asyncio.run(scenario())


def test_cancellation_is_mapped_to_canonical_error() -> None:
    async def scenario() -> None:
        spec = CapabilitySpec(
            capability_id="tool.cancel",
            name="Cancel",
            input_schema={"type": "object"},
            health=HealthStatus.HEALTHY,
        )
        registry = CapabilityRegistry()
        await registry.register_provider(StaticProvider("cancelled", spec, cancel=True))
        with pytest.raises(ContractError) as caught:
            await CapabilityInvoker(registry).invoke(
                _request("tool.cancel", arguments={"message": "x"})
            )
        assert caught.value.code is ErrorCode.CANCELLED

    asyncio.run(scenario())


def test_permission_denied_before_provider_execution() -> None:
    async def scenario() -> None:
        spec = CapabilitySpec(
            capability_id="tool.secure",
            name="Secure",
            input_schema={"type": "object"},
            required_permissions=("workspace.write",),
            health=HealthStatus.HEALTHY,
        )
        provider = StaticProvider("secure", spec)
        registry = CapabilityRegistry()
        await registry.register_provider(provider)

        with pytest.raises(ContractError) as caught:
            await CapabilityInvoker(registry).invoke(
                _request("tool.secure", arguments={"message": "x"})
            )
        assert caught.value.code is ErrorCode.FORBIDDEN
        assert provider.calls == 0

    asyncio.run(scenario())


def test_approval_required_without_governance_binding_is_contract_violation() -> None:
    async def policy(
        request: CapabilityInvocation, capability: CapabilitySpec
    ) -> PolicyDecision:
        return PolicyDecision.REQUIRE_APPROVAL

    async def scenario() -> None:
        registry = CapabilityRegistry()
        await registry.register_provider(NativeEchoProvider())
        with pytest.raises(ContractError) as caught:
            await CapabilityInvoker(registry, policy_hook=policy).invoke(_request())
        assert caught.value.code is ErrorCode.CONTRACT_VIOLATION

    asyncio.run(scenario())


def test_approval_required_is_bound_to_canonical_tool_invocation() -> None:
    async def policy(
        request: CapabilityInvocation, capability: CapabilitySpec
    ) -> PolicyDecision:
        return PolicyDecision.REQUIRE_APPROVAL

    async def approve(
        request: CapabilityInvocation,
        capability: CapabilitySpec,
        canonical_invocation: DomainToolInvocation,
    ) -> bool:
        assert canonical_invocation.id.startswith("tool_invocation_")
        return True

    async def scenario() -> None:
        registry = CapabilityRegistry()
        await registry.register_provider(NativeEchoProvider())
        observer = RecordingObserver()
        result = await CapabilityInvoker(
            registry,
            policy_hook=policy,
            governance_binding_hook=_governance_binding,
            approval_hook=approve,
            observer=observer,
        ).invoke(_request())

        assert result.output == {"message": "hello"}
        assert result.canonical_tool_invocation_id is not None
        assert result.canonical_tool_invocation_id.startswith("tool_invocation_")
        assert observer.records[-1].canonical_tool_invocation_id == result.canonical_tool_invocation_id

    asyncio.run(scenario())


def test_approval_required_but_rejected_fails_before_execution() -> None:
    async def policy(
        request: CapabilityInvocation, capability: CapabilitySpec
    ) -> PolicyDecision:
        return PolicyDecision.REQUIRE_APPROVAL

    async def reject(
        request: CapabilityInvocation,
        capability: CapabilitySpec,
        canonical_invocation: DomainToolInvocation,
    ) -> bool:
        return False

    async def scenario() -> None:
        registry = CapabilityRegistry()
        await registry.register_provider(NativeEchoProvider())
        with pytest.raises(ContractError) as caught:
            await CapabilityInvoker(
                registry,
                policy_hook=policy,
                governance_binding_hook=_governance_binding,
                approval_hook=reject,
            ).invoke(_request())
        assert caught.value.code is ErrorCode.FORBIDDEN
        assert caught.value.details["approval_required"] is True
        canonical_id = caught.value.details["canonical_tool_invocation_id"]
        assert isinstance(canonical_id, str)
        assert canonical_id.startswith("tool_invocation_")

    asyncio.run(scenario())


def test_provider_error_is_mapped() -> None:
    async def scenario() -> None:
        spec = CapabilitySpec(
            capability_id="tool.fail",
            name="Fail",
            input_schema={"type": "object"},
            health=HealthStatus.HEALTHY,
        )
        registry = CapabilityRegistry()
        await registry.register_provider(StaticProvider("broken", spec, fail=True))
        with pytest.raises(ContractError) as caught:
            await CapabilityInvoker(registry).invoke(
                _request("tool.fail", arguments={"message": "x"})
            )
        assert caught.value.code is ErrorCode.BACKEND_ERROR
        assert caught.value.provider_id == "broken"

    asyncio.run(scenario())


def test_duplicate_provider_registration_is_explicit_conflict() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        provider = NativeEchoProvider()
        await registry.register_provider(provider)
        with pytest.raises(ContractError) as caught:
            await registry.register_provider(provider)
        assert caught.value.code is ErrorCode.CONFLICT

    asyncio.run(scenario())


def test_conflicting_capability_registration_is_rejected() -> None:
    async def scenario() -> None:
        first = CapabilitySpec(
            capability_id="tool.same",
            name="Same",
            input_schema={"type": "object"},
            health=HealthStatus.HEALTHY,
        )
        second = CapabilitySpec(
            capability_id="tool.same",
            name="Same",
            input_schema={"type": "string"},
            health=HealthStatus.HEALTHY,
        )
        registry = CapabilityRegistry()
        await registry.register_provider(StaticProvider("a", first, priority=2))
        with pytest.raises(ContractError) as caught:
            await registry.register_provider(StaticProvider("b", second, priority=1))
        assert caught.value.code is ErrorCode.CONFLICT

    asyncio.run(scenario())


def test_capability_version_mismatch_is_clear() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        await registry.register_provider(NativeEchoProvider())
        with pytest.raises(ContractError) as caught:
            registry.resolve("tool.echo", version="2.0")
        assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
        assert caught.value.details["available_versions"] == ["1.0"]

    asyncio.run(scenario())


def test_ambiguous_equal_priority_providers_are_not_silently_selected() -> None:
    async def scenario() -> None:
        spec = CapabilitySpec(
            capability_id="tool.redundant",
            name="Redundant",
            input_schema={"type": "object"},
            health=HealthStatus.HEALTHY,
        )
        registry = CapabilityRegistry()
        await registry.register_provider(StaticProvider("a", spec))
        await registry.register_provider(StaticProvider("b", spec))
        with pytest.raises(ContractError) as caught:
            registry.resolve("tool.redundant")
        assert caught.value.code is ErrorCode.CONFLICT

    asyncio.run(scenario())
