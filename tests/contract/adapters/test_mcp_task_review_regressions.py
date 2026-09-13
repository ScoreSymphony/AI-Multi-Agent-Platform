from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig, MCPTool, MCPToolProvider
from ai_multi_agent_platform.adapters.mcp_tasks import (
    MCP_TASKS_PROTOCOL_REVISION,
    InMemoryMCPTaskBindingStore,
    MCPImmediateToolResult,
    MCPTaskBinding,
    MCPTaskCallResult,
    MCPTaskSnapshot,
    MCPTaskStarted,
    MCPTaskStatus,
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


def _time(offset: int = 0) -> datetime:
    return datetime(2026, 9, 13, 20, 30, tzinfo=UTC) + timedelta(seconds=offset)


def _snapshot(
    status: MCPTaskStatus,
    *,
    task_id: str = "review-task-secret",
    offset: int = 0,
    result: JsonValue = None,
    poll_interval_ms: int = 0,
) -> MCPTaskSnapshot:
    return MCPTaskSnapshot(
        task_id=task_id,
        status=status,
        created_at=_time(),
        last_updated_at=_time(offset),
        ttl_ms=60_000,
        poll_interval_ms=poll_interval_ms,
        result=result,
    )


def _request(invocation_id: str) -> CapabilityInvocation:
    project_id = new_id("project")
    context = OperationContext(
        correlation_id=f"corr-{invocation_id}",
        owner_type="user",
        owner_id="user-964-review",
        project_id=project_id,
    )
    return CapabilityInvocation(
        invocation_id=invocation_id,
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


class _ReviewTaskClient:
    task_protocol_revision = MCP_TASKS_PROTOCOL_REVISION

    def __init__(
        self,
        *,
        supports_tasks: bool = True,
        outcomes: list[MCPTaskCallResult] | None = None,
        polls: list[MCPTaskSnapshot | Exception] | None = None,
    ) -> None:
        self.supports_tasks_value = supports_tasks
        self.outcomes = list(outcomes or [])
        self.polls = list(polls or [])
        self.supports_calls = 0
        self.task_calls = 0
        self.sync_calls = 0
        self.get_calls: list[str] = []
        self.cancel_calls: list[str] = []

    async def list_tools(self) -> tuple[MCPTool, ...]:
        return (
            MCPTool(
                name="lookup",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {"answer": {"type": "integer"}},
                    "required": ["answer"],
                    "additionalProperties": False,
                },
            ),
        )

    async def call_tool(self, name: str, arguments: dict[str, JsonValue]) -> JsonValue:
        self.sync_calls += 1
        return {"answer": -1}

    async def ping(self) -> bool:
        return True

    async def supports_tasks(self) -> bool:
        self.supports_calls += 1
        return self.supports_tasks_value

    async def call_tool_with_tasks(
        self,
        name: str,
        arguments: dict[str, JsonValue],
    ) -> MCPTaskCallResult:
        self.task_calls += 1
        if not self.outcomes:
            raise AssertionError("unexpected task-aware tools/call")
        return self.outcomes.pop(0)

    async def get_task(self, task_id: str) -> MCPTaskSnapshot:
        self.get_calls.append(task_id)
        if not self.polls:
            raise AssertionError("unexpected tasks/get")
        item = self.polls.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def update_task(
        self,
        task_id: str,
        input_responses: dict[str, JsonValue],
    ) -> None:
        raise AssertionError("unexpected tasks/update")

    async def cancel_task(self, task_id: str) -> None:
        self.cancel_calls.append(task_id)


class _SettlingBindStore(InMemoryMCPTaskBindingStore):
    """Persist first, then surface cancellation like the dedicated SQLite worker."""

    def __init__(self) -> None:
        super().__init__()
        self.bind_started = asyncio.Event()

    async def bind(self, binding: MCPTaskBinding) -> MCPTaskBinding:
        stored = await super().bind(binding)
        self.bind_started.set()
        await asyncio.Future[None]()
        return stored


async def _register(
    client: _ReviewTaskClient,
    *,
    store: InMemoryMCPTaskBindingStore | None = None,
) -> tuple[MCPToolProvider, CapabilityRegistry]:
    provider = MCPToolProvider(
        MCPServerConfig(
            server_id="review-regressions",
            endpoint="http://localhost.invalid/mcp",
            capability_id_overrides={"lookup": "tool.lookup"},
            enable_tasks=True,
            task_default_poll_interval_ms=0,
        ),
        client,
        task_binding_store=store,
    )
    registry = CapabilityRegistry()
    await registry.register_provider(provider)
    return provider, registry


@pytest.mark.asyncio
async def test_existing_binding_is_reconciled_before_synchronous_fallback() -> None:
    store = InMemoryMCPTaskBindingStore()
    request = _request("invoke-review-existing-binding")
    first_client = _ReviewTaskClient(
        outcomes=[MCPTaskStarted(_snapshot(MCPTaskStatus.WORKING))],
        polls=[ContractError(ErrorCode.UNAVAILABLE, "transport disconnected", retryable=True)],
    )
    _, first_registry = await _register(first_client, store=store)

    with pytest.raises(ContractError) as first_error:
        await CapabilityInvoker(first_registry).invoke(request)
    assert first_error.value.code is ErrorCode.UNAVAILABLE

    recovery_client = _ReviewTaskClient(
        supports_tasks=False,
        polls=[
            _snapshot(
                MCPTaskStatus.COMPLETED,
                offset=2,
                result={"structuredContent": {"answer": 42}, "isError": False},
            )
        ],
    )
    _, recovery_registry = await _register(recovery_client, store=store)

    recovered = await CapabilityInvoker(recovery_registry).invoke(request)

    assert recovered.output == {"answer": 42}
    assert recovery_client.supports_calls == 0
    assert recovery_client.sync_calls == 0
    assert recovery_client.task_calls == 0
    assert recovery_client.get_calls == ["review-task-secret"]


@pytest.mark.asyncio
async def test_cancellation_during_binding_cancels_the_new_external_task() -> None:
    store = _SettlingBindStore()
    client = _ReviewTaskClient(
        outcomes=[
            MCPTaskStarted(
                _snapshot(
                    MCPTaskStatus.WORKING,
                    task_id="cancel-during-bind-task",
                    poll_interval_ms=60_000,
                )
            )
        ]
    )
    provider, registry = await _register(client, store=store)
    request = _request("invoke-review-cancel-during-bind")
    operation = asyncio.create_task(CapabilityInvoker(registry).invoke(request))
    await store.bind_started.wait()

    operation.cancel()
    with pytest.raises(ContractError) as caught:
        await operation

    assert caught.value.code is ErrorCode.CANCELLED
    assert client.cancel_calls == ["cancel-during-bind-task"]
    binding = await store.get("mcp:review-regressions", request.invocation_id)
    assert binding is not None
    assert binding.cancellation_requested_at is not None
    assert binding.cancellation_acknowledged is True
    assert provider._active_task_bindings == {}


@pytest.mark.asyncio
async def test_completed_invocations_release_per_invocation_task_locks() -> None:
    client = _ReviewTaskClient(
        outcomes=[MCPImmediateToolResult({"answer": index}) for index in range(25)]
    )
    provider, registry = await _register(client)
    invoker = CapabilityInvoker(registry)

    for index in range(25):
        result = await invoker.invoke(_request(f"invoke-review-lock-{index}"))
        assert result.output == {"answer": index}

    assert provider._task_locks == {}
    assert provider._task_lock_users == {}


@pytest.mark.asyncio
async def test_completed_async_invocations_release_active_task_bindings() -> None:
    client = _ReviewTaskClient(
        outcomes=[
            MCPTaskStarted(
                _snapshot(
                    MCPTaskStatus.WORKING,
                    task_id="completed-cache-task",
                )
            )
        ],
        polls=[
            _snapshot(
                MCPTaskStatus.COMPLETED,
                task_id="completed-cache-task",
                offset=1,
                result={"structuredContent": {"answer": 42}, "isError": False},
            )
        ],
    )
    provider, registry = await _register(client)

    result = await CapabilityInvoker(registry).invoke(_request("invoke-review-completed-cache"))

    assert result.output == {"answer": 42}
    assert provider._active_task_bindings == {}
