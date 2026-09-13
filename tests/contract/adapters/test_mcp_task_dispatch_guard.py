from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig, MCPTool, MCPToolProvider
from ai_multi_agent_platform.adapters.mcp_tasks import (
    MCP_TASKS_PROTOCOL_REVISION,
    InMemoryMCPTaskBindingStore,
    MCPImmediateToolResult,
    MCPTaskCallResult,
    MCPTaskSnapshot,
    SqliteMCPTaskBindingStore,
)
from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistry,
    InvocationTrace,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext
from ai_multi_agent_platform.domain import new_id


class _BlockingTaskClient:
    task_protocol_revision = MCP_TASKS_PROTOCOL_REVISION

    def __init__(self, *, block_dispatch: bool = False) -> None:
        self.block_dispatch = block_dispatch
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.task_calls = 0

    async def list_tools(self) -> tuple[MCPTool, ...]:
        return (
            MCPTool(
                name="lookup",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            ),
        )

    async def call_tool(self, name: str, arguments: dict[str, JsonValue]) -> JsonValue:
        raise AssertionError("unexpected synchronous tools/call")

    async def ping(self) -> bool:
        return True

    async def supports_tasks(self) -> bool:
        return True

    async def call_tool_with_tasks(
        self,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> MCPTaskCallResult:
        self.task_calls += 1
        self.entered.set()
        if self.block_dispatch:
            await self.release.wait()
        return MCPImmediateToolResult({"answer": 7})

    async def get_task(self, task_id: str) -> MCPTaskSnapshot:
        raise AssertionError("unexpected tasks/get")

    async def update_task(
        self,
        task_id: str,
        input_responses: dict[str, JsonValue],
    ) -> None:
        raise AssertionError("unexpected tasks/update")

    async def cancel_task(self, task_id: str) -> None:
        raise AssertionError("unexpected tasks/cancel")


async def _registry(
    client: _BlockingTaskClient,
    store: InMemoryMCPTaskBindingStore | SqliteMCPTaskBindingStore,
) -> CapabilityRegistry:
    provider = MCPToolProvider(
        MCPServerConfig(
            server_id="dispatch-guard",
            endpoint="http://localhost.invalid/mcp",
            capability_id_overrides={"lookup": "tool.lookup"},
            enable_tasks=True,
        ),
        client,
        task_binding_store=store,
    )
    registry = CapabilityRegistry()
    await registry.register_provider(provider)
    return registry


def _request(*, owner: bool = True) -> CapabilityInvocation:
    project_id = new_id("project")
    context = OperationContext(
        correlation_id="corr-dispatch-guard",
        owner_type="user" if owner else None,
        owner_id="user-964" if owner else None,
        project_id=project_id,
    )
    return CapabilityInvocation(
        invocation_id="invoke-964-dispatch-guard",
        capability_id="tool.lookup",
        arguments={"query": "abc"},
        context=context,
        trace=InvocationTrace(
            correlation_id=context.correlation_id,
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=project_id,
        ),
    )


@pytest.mark.asyncio
async def test_shared_store_allows_only_one_external_dispatch_for_same_attempt() -> None:
    store = InMemoryMCPTaskBindingStore()
    first_client = _BlockingTaskClient(block_dispatch=True)
    second_client = _BlockingTaskClient()
    first_registry = await _registry(first_client, store)
    second_registry = await _registry(second_client, store)
    request = _request()

    first = asyncio.create_task(CapabilityInvoker(first_registry).invoke(request))
    await first_client.entered.wait()

    with pytest.raises(ContractError) as concurrent:
        await CapabilityInvoker(second_registry).invoke(request)

    assert concurrent.value.code is ErrorCode.CONFLICT
    assert concurrent.value.details == {
        "mcp_task_dispatch_claimed": True,
        "automatic_redispatch_blocked": True,
    }
    assert second_client.task_calls == 0

    first_client.release.set()
    result = await first
    assert result.output == {"answer": 7}
    assert first_client.task_calls == 1

    # A task-capable call that completed synchronously still consumed this exact canonical attempt.
    # Reusing the same invocation ID must fail closed rather than repeat a side effect.
    with pytest.raises(ContractError) as replay:
        await CapabilityInvoker(second_registry).invoke(request)
    assert replay.value.code is ErrorCode.CONFLICT
    assert second_client.task_calls == 0


@pytest.mark.asyncio
async def test_sqlite_dispatch_claim_survives_restart_and_blocks_ambiguous_redispatch(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "mcp-task-dispatch.db"
    first = SqliteMCPTaskBindingStore(db_path)
    assert await first.claim_dispatch("mcp:dispatch-guard", "invoke-ambiguous") is True
    first.close()

    second = SqliteMCPTaskBindingStore(db_path)
    try:
        assert await second.claim_dispatch("mcp:dispatch-guard", "invoke-ambiguous") is False
        assert await second.get("mcp:dispatch-guard", "invoke-ambiguous") is None
    finally:
        second.close()


@pytest.mark.asyncio
async def test_task_context_is_validated_before_external_dispatch() -> None:
    store = InMemoryMCPTaskBindingStore()
    client = _BlockingTaskClient()
    registry = await _registry(client, store)

    with pytest.raises(ContractError) as caught:
        await CapabilityInvoker(registry).invoke(_request(owner=False))

    assert caught.value.code is ErrorCode.CONTRACT_VIOLATION
    assert client.task_calls == 0
