from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.adapters.mcp import (
    MCPServerConfig,
    MCPTaskInputHandler,
    MCPTool,
    MCPToolProvider,
)
from ai_multi_agent_platform.adapters.mcp_tasks import (
    InMemoryMCPTaskBindingStore,
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
from ai_multi_agent_platform.contracts.types import (
    JsonValue,
    OperationContext,
    OperationControl,
    ToolInvocation,
)
from ai_multi_agent_platform.domain import new_id


def _time(offset: int = 0) -> datetime:
    return datetime(2026, 9, 13, 19, 0, tzinfo=UTC) + timedelta(seconds=offset)


def _snapshot(
    status: MCPTaskStatus,
    *,
    task_id: str,
    offset: int = 0,
    result: JsonValue = None,
    input_requests: dict[str, JsonValue] | None = None,
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
        input_requests=input_requests,
    )


def _request(
    *,
    invocation_id: str,
    idempotency_key: str | None = None,
) -> CapabilityInvocation:
    project_id = new_id("project")
    context = OperationContext(
        correlation_id=f"corr-{invocation_id}",
        owner_type="user",
        owner_id="user-964",
        project_id=project_id,
        control=OperationControl(idempotency_key=idempotency_key),
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


class _HardeningClient:
    task_protocol_revision = "2026-07-28"

    def __init__(
        self,
        *,
        outcomes: list[MCPTaskCallResult],
        polls: list[MCPTaskSnapshot] | None = None,
        cancel_error: ContractError | None = None,
    ) -> None:
        self.outcomes = list(outcomes)
        self.polls = list(polls or [])
        self.cancel_error = cancel_error
        self.task_calls = 0
        self.update_calls: list[tuple[str, dict[str, JsonValue]]] = []
        self.cancel_calls: list[str] = []
        self.started = asyncio.Event()

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
        return {"answer": -1}

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
        self.started.set()
        if not self.outcomes:
            raise AssertionError("unexpected task-aware tools/call")
        return self.outcomes.pop(0)

    async def get_task(self, task_id: str) -> MCPTaskSnapshot:
        if not self.polls:
            raise AssertionError("unexpected tasks/get")
        return self.polls.pop(0)

    async def update_task(
        self,
        task_id: str,
        input_responses: dict[str, JsonValue],
    ) -> None:
        self.update_calls.append((task_id, input_responses))

    async def cancel_task(self, task_id: str) -> None:
        self.cancel_calls.append(task_id)
        if self.cancel_error is not None:
            raise self.cancel_error


class _WrongRevisionClient(_HardeningClient):
    task_protocol_revision = "2026-06-30"


async def _registry(
    client: _HardeningClient,
    store: InMemoryMCPTaskBindingStore,
    *,
    input_handler: MCPTaskInputHandler | None = None,
) -> CapabilityRegistry:
    provider = MCPToolProvider(
        MCPServerConfig(
            server_id="hardening",
            endpoint="http://localhost.invalid/mcp",
            capability_id_overrides={"lookup": "tool.lookup"},
            enable_tasks=True,
            task_default_poll_interval_ms=0,
        ),
        client,
        task_binding_store=store,
        task_input_handler=input_handler,
    )
    registry = CapabilityRegistry()
    await registry.register_provider(provider)
    return registry


@pytest.mark.asyncio
async def test_binding_persists_correlation_and_idempotency_context() -> None:
    store = InMemoryMCPTaskBindingStore()
    client = _HardeningClient(
        outcomes=[
            MCPTaskStarted(
                _snapshot(
                    MCPTaskStatus.COMPLETED,
                    task_id="context-task",
                    result={"structuredContent": {"answer": 1}, "isError": False},
                )
            )
        ]
    )
    registry = await _registry(client, store)
    request = _request(invocation_id="invoke-context", idempotency_key="idem-964")

    await CapabilityInvoker(registry).invoke(request)

    binding = await store.get("mcp:hardening", request.invocation_id)
    assert binding is not None
    assert binding.correlation_id == request.context.correlation_id
    assert binding.idempotency_key == "idem-964"


@pytest.mark.asyncio
async def test_duplicate_input_required_poll_does_not_repeat_governed_action() -> None:
    calls = 0

    async def input_handler(
        invocation: ToolInvocation,
        snapshot: MCPTaskSnapshot,
    ) -> dict[str, JsonValue]:
        nonlocal calls
        calls += 1
        assert snapshot.input_requests == {"request-1": {"method": "elicitation/create"}}
        return {"request-1": {"action": "accept", "content": {"input": "yes"}}}

    request_state = {"request-1": {"method": "elicitation/create"}}
    store = InMemoryMCPTaskBindingStore()
    client = _HardeningClient(
        outcomes=[
            MCPTaskStarted(
                _snapshot(
                    MCPTaskStatus.INPUT_REQUIRED,
                    task_id="input-task",
                    input_requests=request_state,
                )
            )
        ],
        polls=[
            _snapshot(
                MCPTaskStatus.INPUT_REQUIRED,
                task_id="input-task",
                offset=1,
                input_requests=request_state,
            ),
            _snapshot(
                MCPTaskStatus.COMPLETED,
                task_id="input-task",
                offset=2,
                result={"structuredContent": {"answer": 2}, "isError": False},
            ),
        ],
    )
    registry = await _registry(client, store, input_handler=input_handler)
    request = _request(invocation_id="invoke-input-dedup")

    result = await CapabilityInvoker(registry).invoke(request)

    assert result.output == {"answer": 2}
    assert calls == 1
    assert len(client.update_calls) == 1
    binding = await store.get("mcp:hardening", request.invocation_id)
    assert binding is not None
    assert binding.responded_input_keys == ("request-1",)


@pytest.mark.asyncio
async def test_stale_terminal_poll_is_ignored_in_favor_of_newer_external_observation() -> None:
    store = InMemoryMCPTaskBindingStore()
    client = _HardeningClient(
        outcomes=[
            MCPTaskStarted(
                _snapshot(
                    MCPTaskStatus.WORKING,
                    task_id="stale-task",
                    offset=10,
                )
            )
        ],
        polls=[
            _snapshot(
                MCPTaskStatus.COMPLETED,
                task_id="stale-task",
                offset=9,
                result={"structuredContent": {"answer": 1}, "isError": False},
            ),
            _snapshot(
                MCPTaskStatus.COMPLETED,
                task_id="stale-task",
                offset=11,
                result={"structuredContent": {"answer": 2}, "isError": False},
            ),
        ],
    )
    registry = await _registry(client, store)

    result = await CapabilityInvoker(registry).invoke(_request(invocation_id="invoke-stale"))

    assert result.output == {"answer": 2}


@pytest.mark.asyncio
async def test_mismatched_task_protocol_revision_fails_closed() -> None:
    store = InMemoryMCPTaskBindingStore()
    client = _WrongRevisionClient(outcomes=[])
    registry = await _registry(client, store)

    with pytest.raises(ContractError) as caught:
        await CapabilityInvoker(registry).invoke(_request(invocation_id="invoke-revision"))

    assert caught.value.code is ErrorCode.CONTRACT_VIOLATION
    assert client.task_calls == 0


@pytest.mark.asyncio
async def test_platform_cancellation_records_provider_cancellation_unavailability() -> None:
    store = InMemoryMCPTaskBindingStore()
    client = _HardeningClient(
        outcomes=[
            MCPTaskStarted(
                _snapshot(
                    MCPTaskStatus.WORKING,
                    task_id="cancel-unavailable-task",
                    poll_interval_ms=60_000,
                )
            )
        ],
        cancel_error=ContractError(
            ErrorCode.UNAVAILABLE,
            "provider unavailable during cancellation",
            retryable=True,
        ),
    )
    registry = await _registry(client, store)
    request = _request(invocation_id="invoke-cancel-unavailable")
    operation = asyncio.create_task(CapabilityInvoker(registry).invoke(request))
    await client.started.wait()
    await asyncio.sleep(0)

    operation.cancel()
    with pytest.raises(ContractError) as caught:
        await operation

    assert caught.value.code is ErrorCode.CANCELLED
    binding = await store.get("mcp:hardening", request.invocation_id)
    assert binding is not None
    assert binding.cancellation_requested_at is not None
    assert binding.cancellation_acknowledged is False
    assert binding.cancellation_error_code == ErrorCode.UNAVAILABLE.value
    assert client.cancel_calls == ["cancel-unavailable-task"]


@pytest.mark.asyncio
async def test_new_canonical_run_attempt_creates_a_distinct_external_task_binding() -> None:
    store = InMemoryMCPTaskBindingStore()
    client = _HardeningClient(
        outcomes=[
            MCPTaskStarted(
                _snapshot(
                    MCPTaskStatus.COMPLETED,
                    task_id="external-attempt-1",
                    result={"structuredContent": {"answer": 1}, "isError": False},
                )
            ),
            MCPTaskStarted(
                _snapshot(
                    MCPTaskStatus.COMPLETED,
                    task_id="external-attempt-2",
                    offset=1,
                    result={"structuredContent": {"answer": 2}, "isError": False},
                )
            ),
        ]
    )
    registry = await _registry(client, store)
    first = _request(invocation_id="invoke-retry-1")
    second = _request(invocation_id="invoke-retry-2")

    first_result = await CapabilityInvoker(registry).invoke(first)
    second_result = await CapabilityInvoker(registry).invoke(second)

    assert first_result.output == {"answer": 1}
    assert second_result.output == {"answer": 2}
    first_binding = await store.get("mcp:hardening", first.invocation_id)
    second_binding = await store.get("mcp:hardening", second.invocation_id)
    assert first_binding is not None
    assert second_binding is not None
    assert first_binding.canonical_run_id != second_binding.canonical_run_id
    assert first_binding.external_task_id == "external-attempt-1"
    assert second_binding.external_task_id == "external-attempt-2"
    assert client.task_calls == 2
