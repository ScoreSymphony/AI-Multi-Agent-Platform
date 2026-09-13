from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig, MCPTool, MCPToolProvider
from ai_multi_agent_platform.adapters.mcp_stateless import (
    MCPStatelessHTTPClient,
    _WireResponse,
)
from ai_multi_agent_platform.adapters.mcp_tasks import (
    MCP_TASKS_EXTENSION_ID,
    MCP_TASKS_PROTOCOL_REVISION,
    InMemoryMCPTaskBindingStore,
    MCPTaskCallResult,
    MCPTaskSnapshot,
    MCPTaskStarted,
    MCPTaskStatus,
    SqliteMCPTaskBindingStore,
)
from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistry,
    InvocationRecord,
    InvocationTrace,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext, ToolInvocation
from ai_multi_agent_platform.domain import new_id


def _time(offset: int = 0) -> datetime:
    return datetime(2026, 9, 13, 19, 0, tzinfo=UTC) + timedelta(seconds=offset)


def _snapshot(
    status: MCPTaskStatus,
    *,
    task_id: str = "external-task-secret",
    offset: int = 0,
    result: JsonValue = None,
    error: JsonValue = None,
    input_requests: dict[str, JsonValue] | None = None,
    poll_interval_ms: int | None = 0,
) -> MCPTaskSnapshot:
    return MCPTaskSnapshot(
        task_id=task_id,
        status=status,
        created_at=_time(),
        last_updated_at=_time(offset),
        ttl_ms=60_000,
        poll_interval_ms=poll_interval_ms,
        result=result,
        error=error,
        input_requests=input_requests,
    )


def _request(
    *,
    invocation_id: str = "invoke-964-1",
    task_id: str | None = None,
    run_id: str | None = None,
) -> CapabilityInvocation:
    canonical_task_id = task_id or new_id("task")
    canonical_run_id = run_id or new_id("run")
    project_id = new_id("project")
    context = OperationContext(
        correlation_id="corr-964",
        owner_type="user",
        owner_id="user-964",
        project_id=project_id,
    )
    return CapabilityInvocation(
        invocation_id=invocation_id,
        capability_id="tool.lookup",
        arguments={"query": "abc"},
        context=context,
        trace=InvocationTrace(
            correlation_id=context.correlation_id,
            task_id=canonical_task_id,
            run_id=canonical_run_id,
            agent_id=new_id("agent"),
            project_id=project_id,
        ),
    )


class _RecordingObserver:
    def __init__(self) -> None:
        self.records: list[InvocationRecord] = []

    async def record(self, record: InvocationRecord) -> None:
        self.records.append(record)


class _FakeTaskClient:
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
        self.task_calls = 0
        self.sync_calls = 0
        self.get_calls: list[str] = []
        self.cancel_calls: list[str] = []
        self.update_calls: list[tuple[str, dict[str, JsonValue]]] = []
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
        self.sync_calls += 1
        return {"answer": 7}

    async def ping(self) -> bool:
        return True

    async def supports_tasks(self) -> bool:
        return self.supports_tasks_value

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
        self.update_calls.append((task_id, input_responses))

    async def cancel_task(self, task_id: str) -> None:
        self.cancel_calls.append(task_id)


async def _register(
    client: _FakeTaskClient,
    *,
    store: InMemoryMCPTaskBindingStore | SqliteMCPTaskBindingStore | None = None,
    input_handler: Any = None,
    default_poll_ms: int = 0,
) -> tuple[MCPToolProvider, CapabilityRegistry]:
    provider = MCPToolProvider(
        MCPServerConfig(
            server_id="tasks-test",
            endpoint="http://localhost.invalid/mcp",
            capability_id_overrides={"lookup": "tool.lookup"},
            enable_tasks=True,
            task_default_poll_interval_ms=default_poll_ms,
        ),
        client,
        task_binding_store=store,
        task_input_handler=input_handler,
    )
    registry = CapabilityRegistry()
    await registry.register_provider(provider)
    return provider, registry


@pytest.mark.asyncio
async def test_server_without_tasks_uses_ordinary_synchronous_path() -> None:
    client = _FakeTaskClient(supports_tasks=False)
    _, registry = await _register(client)

    result = await CapabilityInvoker(registry).invoke(_request())

    assert result.output == {"answer": 7}
    assert client.sync_calls == 1
    assert client.task_calls == 0


@pytest.mark.asyncio
async def test_async_task_stays_one_canonical_invocation_and_redacts_external_handle() -> None:
    store = InMemoryMCPTaskBindingStore()
    client = _FakeTaskClient(
        outcomes=[MCPTaskStarted(_snapshot(MCPTaskStatus.WORKING))],
        polls=[
            _snapshot(
                MCPTaskStatus.COMPLETED,
                offset=2,
                result={"structuredContent": {"answer": 42}, "isError": False},
            )
        ],
    )
    _, registry = await _register(client, store=store)
    observer = _RecordingObserver()
    request = _request()

    result = await CapabilityInvoker(registry, observer=observer).invoke(request)

    assert result.output == {"answer": 42}
    assert client.task_calls == 1
    assert client.get_calls == ["external-task-secret"]
    assert [record.status.value for record in observer.records] == ["running", "succeeded"]
    binding = await store.get("mcp:tasks-test", request.invocation_id)
    assert binding is not None
    assert binding.canonical_task_id == request.trace.task_id
    assert binding.canonical_run_id == request.trace.run_id
    assert binding.external_task_id == "external-task-secret"
    task_metadata = next(item for item in result.adapter_metadata if item.namespace == "mcp.tasks")
    assert (
        task_metadata.values["external_task_id_sha256"]
        == hashlib.sha256(b"external-task-secret").hexdigest()
    )
    assert "external-task-secret" not in repr(result.adapter_metadata)


@pytest.mark.asyncio
async def test_restart_reconciles_existing_binding_without_second_tools_call(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "mcp-task-bindings.db"
    store1 = SqliteMCPTaskBindingStore(db_path)
    request = _request()
    client1 = _FakeTaskClient(
        outcomes=[MCPTaskStarted(_snapshot(MCPTaskStatus.WORKING))],
        polls=[
            ContractError(
                ErrorCode.UNAVAILABLE,
                "transport disconnected",
                retryable=True,
            )
        ],
    )
    _, registry1 = await _register(client1, store=store1)

    with pytest.raises(ContractError) as first_error:
        await CapabilityInvoker(registry1).invoke(request)
    assert first_error.value.code is ErrorCode.UNAVAILABLE
    assert client1.task_calls == 1

    store2 = SqliteMCPTaskBindingStore(db_path)
    client2 = _FakeTaskClient(
        polls=[
            _snapshot(
                MCPTaskStatus.COMPLETED,
                offset=3,
                result={"structuredContent": {"answer": 99}, "isError": False},
            )
        ]
    )
    _, registry2 = await _register(client2, store=store2)

    recovered = await CapabilityInvoker(registry2).invoke(request)

    assert recovered.output == {"answer": 99}
    assert client2.task_calls == 0
    assert client2.get_calls == ["external-task-secret"]


@pytest.mark.asyncio
async def test_bound_task_cannot_be_reused_under_another_canonical_run() -> None:
    store = InMemoryMCPTaskBindingStore()
    first = _request()
    client1 = _FakeTaskClient(
        outcomes=[MCPTaskStarted(_snapshot(MCPTaskStatus.WORKING))],
        polls=[ContractError(ErrorCode.UNAVAILABLE, "offline", retryable=True)],
    )
    _, registry1 = await _register(client1, store=store)
    with pytest.raises(ContractError):
        await CapabilityInvoker(registry1).invoke(first)

    different_run = _request(
        invocation_id=first.invocation_id,
        task_id=first.trace.task_id,
        run_id=new_id("run"),
    )
    client2 = _FakeTaskClient()
    _, registry2 = await _register(client2, store=store)

    with pytest.raises(ContractError) as caught:
        await CapabilityInvoker(registry2).invoke(different_run)

    assert caught.value.code is ErrorCode.FORBIDDEN
    assert client2.task_calls == 0
    assert client2.get_calls == []


@pytest.mark.asyncio
async def test_input_required_fails_closed_and_cancels_bound_task_without_handler() -> None:
    client = _FakeTaskClient(
        outcomes=[
            MCPTaskStarted(
                _snapshot(
                    MCPTaskStatus.INPUT_REQUIRED,
                    input_requests={
                        "name": {
                            "method": "elicitation/create",
                            "params": {"message": "name?"},
                        }
                    },
                )
            )
        ]
    )
    _, registry = await _register(client)

    with pytest.raises(ContractError) as caught:
        await CapabilityInvoker(registry).invoke(_request())

    assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert client.cancel_calls == ["external-task-secret"]


@pytest.mark.asyncio
async def test_governed_input_handler_uses_tasks_update_then_continues_polling() -> None:
    async def input_handler(
        invocation: ToolInvocation,
        snapshot: MCPTaskSnapshot,
    ) -> dict[str, JsonValue]:
        assert invocation.run_id is not None
        assert snapshot.input_requests is not None
        return {"name": {"action": "accept", "content": {"input": "Samu"}}}

    client = _FakeTaskClient(
        outcomes=[
            MCPTaskStarted(
                _snapshot(
                    MCPTaskStatus.INPUT_REQUIRED,
                    input_requests={
                        "name": {
                            "method": "elicitation/create",
                            "params": {"message": "name?"},
                        }
                    },
                )
            )
        ],
        polls=[
            _snapshot(MCPTaskStatus.WORKING, offset=1),
            _snapshot(
                MCPTaskStatus.COMPLETED,
                offset=2,
                result={"structuredContent": {"answer": 1}, "isError": False},
            ),
        ],
    )
    _, registry = await _register(client, input_handler=input_handler)

    result = await CapabilityInvoker(registry).invoke(_request())

    assert result.output == {"answer": 1}
    assert client.update_calls == [
        (
            "external-task-secret",
            {"name": {"action": "accept", "content": {"input": "Samu"}}},
        )
    ]


@pytest.mark.asyncio
async def test_platform_coroutine_cancellation_targets_only_the_bound_external_task() -> None:
    client = _FakeTaskClient(
        outcomes=[
            MCPTaskStarted(
                _snapshot(
                    MCPTaskStatus.WORKING,
                    poll_interval_ms=60_000,
                )
            )
        ]
    )
    _, registry = await _register(client)
    operation = asyncio.create_task(CapabilityInvoker(registry).invoke(_request()))
    await client.started.wait()
    await asyncio.sleep(0)

    operation.cancel()
    with pytest.raises(ContractError) as caught:
        await operation

    assert caught.value.code is ErrorCode.CANCELLED
    assert client.cancel_calls == ["external-task-secret"]


@pytest.mark.asyncio
async def test_external_cancel_and_failure_remain_provider_observations() -> None:
    cancelled_client = _FakeTaskClient(
        outcomes=[MCPTaskStarted(_snapshot(MCPTaskStatus.CANCELLED))]
    )
    _, cancelled_registry = await _register(cancelled_client)
    with pytest.raises(ContractError) as cancelled:
        await CapabilityInvoker(cancelled_registry).invoke(_request())
    assert cancelled.value.code is ErrorCode.CANCELLED

    failed_client = _FakeTaskClient(
        outcomes=[
            MCPTaskStarted(
                _snapshot(
                    MCPTaskStatus.FAILED,
                    error={"code": -32603, "message": "upstream failed"},
                )
            )
        ]
    )
    _, failed_registry = await _register(failed_client)
    with pytest.raises(ContractError) as failed:
        await CapabilityInvoker(failed_registry).invoke(_request(invocation_id="invoke-964-failed"))
    assert failed.value.code is ErrorCode.BACKEND_ERROR
    assert failed.value.details["mcp_task_failed"] is True


class _ScriptedStatelessClient(MCPStatelessHTTPClient):
    def __init__(self, responses: list[_WireResponse]) -> None:
        super().__init__(
            MCPServerConfig(
                server_id="wire",
                endpoint="http://localhost.invalid/mcp",
                enable_tasks=True,
            )
        )
        self.responses = responses
        self.requests: list[dict[str, JsonValue]] = []

    def _post_json(self, body: dict[str, JsonValue]) -> _WireResponse:
        self.requests.append(body)
        if not self.responses:
            raise AssertionError("unexpected MCP request")
        return self.responses.pop(0)


def _result(payload: dict[str, Any]) -> _WireResponse:
    return _WireResponse(
        status=200,
        payload={"jsonrpc": "2.0", "id": 1, "result": payload},
    )


@pytest.mark.asyncio
async def test_sep2663_wire_negotiation_polymorphic_call_and_poll() -> None:
    client = _ScriptedStatelessClient(
        [
            _result(
                {
                    "capabilities": {
                        "extensions": {MCP_TASKS_EXTENSION_ID: {}},
                    }
                }
            ),
            _result(
                {
                    "resultType": "task",
                    "taskId": "task-wire-1",
                    "status": "working",
                    "createdAt": "2026-09-13T19:00:00Z",
                    "lastUpdatedAt": "2026-09-13T19:00:00Z",
                    "ttlMs": 60_000,
                    "pollIntervalMs": 10,
                }
            ),
            _result(
                {
                    "resultType": "complete",
                    "taskId": "task-wire-1",
                    "status": "completed",
                    "createdAt": "2026-09-13T19:00:00Z",
                    "lastUpdatedAt": "2026-09-13T19:00:01Z",
                    "ttlMs": 60_000,
                    "pollIntervalMs": 10,
                    "result": {
                        "structuredContent": {"answer": 3},
                        "isError": False,
                    },
                }
            ),
        ]
    )

    assert await client.supports_tasks() is True
    started = await client.call_tool_with_tasks("lookup", {"query": "abc"})
    assert isinstance(started, MCPTaskStarted)
    completed = await client.get_task("task-wire-1")
    assert completed.status is MCPTaskStatus.COMPLETED

    discover_capabilities = client.requests[0]["params"]
    assert isinstance(discover_capabilities, dict)
    discover_meta = discover_capabilities["_meta"]
    assert isinstance(discover_meta, dict)
    discover_client_caps = discover_meta["io.modelcontextprotocol/clientCapabilities"]
    assert discover_client_caps == {}

    tool_params = client.requests[1]["params"]
    assert isinstance(tool_params, dict)
    tool_meta = tool_params["_meta"]
    assert isinstance(tool_meta, dict)
    assert tool_meta["io.modelcontextprotocol/clientCapabilities"] == {
        "extensions": {MCP_TASKS_EXTENSION_ID: {}}
    }
    task_params = client.requests[2]["params"]
    assert isinstance(task_params, dict)
    task_meta = task_params["_meta"]
    assert isinstance(task_meta, dict)
    assert task_meta["io.modelcontextprotocol/clientCapabilities"] == {
        "extensions": {MCP_TASKS_EXTENSION_ID: {}}
    }


def test_task_http_methods_use_mcp_name_routing_header() -> None:
    client = _ScriptedStatelessClient([])
    for method in ("tasks/get", "tasks/update", "tasks/cancel"):
        headers = client._request_headers(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": {"taskId": "task-route-secret"},
            }
        )
        assert headers["Mcp-Name"] == "task-route-secret"


@pytest.mark.asyncio
async def test_unknown_task_status_fails_closed() -> None:
    client = _ScriptedStatelessClient(
        [
            _result(
                {
                    "resultType": "complete",
                    "taskId": "task-wire-unknown",
                    "status": "future_unknown_state",
                    "createdAt": "2026-09-13T19:00:00Z",
                    "lastUpdatedAt": "2026-09-13T19:00:01Z",
                    "ttlMs": 60_000,
                }
            )
        ]
    )

    with pytest.raises(ContractError) as caught:
        await client.get_task("task-wire-unknown")

    assert caught.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE


@pytest.mark.asyncio
async def test_missing_external_task_is_distinct_from_execution_failure() -> None:
    client = _ScriptedStatelessClient(
        [
            _WireResponse(
                status=400,
                payload={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {
                        "code": -32602,
                        "message": "Task not found",
                    },
                },
            )
        ]
    )

    with pytest.raises(ContractError) as caught:
        await client.get_task("expired-task")

    assert caught.value.code is ErrorCode.NOT_FOUND
    assert caught.value.details["mcp_task_lost"] is True


@pytest.mark.asyncio
async def test_tasks_cancel_and_update_are_ack_only_complete_results() -> None:
    client = _ScriptedStatelessClient(
        [
            _result({"resultType": "complete"}),
            _result({"resultType": "complete"}),
        ]
    )

    await client.update_task(
        "task-wire-input",
        {"name": {"action": "accept", "content": {"input": "Samu"}}},
    )
    await client.cancel_task("task-wire-input")

    assert [request["method"] for request in client.requests] == [
        "tasks/update",
        "tasks/cancel",
    ]
    assert client.requests[0]["params"]["taskId"] == "task-wire-input"  # type: ignore[index]
    assert client.requests[1]["params"]["taskId"] == "task-wire-input"  # type: ignore[index]
