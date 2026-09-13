from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ai_multi_agent_platform.adapters.mcp import MCPServerConfig, MCPToolProvider
from ai_multi_agent_platform.adapters.mcp_stateless import (
    MCPStatelessHTTPClient,
    _WireResponse,
)
from ai_multi_agent_platform.adapters.mcp_tasks import (
    MCP_TASKS_EXTENSION_ID,
    InMemoryMCPTaskBindingRepository,
    SqliteMCPTaskBindingRepository,
    binding_for_invocation,
)
from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistry,
    InvocationTrace,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    OperationContext,
    OperationControl,
)
from ai_multi_agent_platform.contracts.types import JsonValue, ToolInvocation
from ai_multi_agent_platform.domain import new_id


class _ScriptedTasksClient(MCPStatelessHTTPClient):
    def __init__(
        self,
        responses: list[_WireResponse],
        *,
        task_bindings: InMemoryMCPTaskBindingRepository
        | SqliteMCPTaskBindingRepository
        | None = None,
        task_input_handler: Any = None,
    ) -> None:
        super().__init__(
            MCPServerConfig(
                server_id="tasks-test",
                endpoint="http://localhost.invalid/mcp",
            ),
            task_bindings=task_bindings,
            task_input_handler=task_input_handler,
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


def _error(code: int, message: str) -> _WireResponse:
    return _WireResponse(
        status=200,
        payload={
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": code, "message": message},
        },
    )


def _tasks_discovery(*, supported: bool = True) -> _WireResponse:
    extensions = {MCP_TASKS_EXTENSION_ID: {}} if supported else {}
    return _result({"capabilities": {"extensions": extensions}})


def _created_task(task_id: str = "external-task-1") -> _WireResponse:
    return _result(
        {
            "resultType": "task",
            "taskId": task_id,
            "status": "working",
            "createdAt": "2026-09-13T18:00:00Z",
            "lastUpdatedAt": "2026-09-13T18:00:00Z",
            "ttlMs": 60000,
            "pollIntervalMs": 0,
        }
    )


def _completed_task(
    task_id: str = "external-task-1",
    *,
    value: int = 42,
) -> _WireResponse:
    return _result(
        {
            "resultType": "complete",
            "taskId": task_id,
            "status": "completed",
            "createdAt": "2026-09-13T18:00:00Z",
            "lastUpdatedAt": "2026-09-13T18:00:01Z",
            "ttlMs": 60000,
            "pollIntervalMs": 0,
            "result": {
                "structuredContent": {"value": value},
                "isError": False,
            },
        }
    )


def _invocation(
    *,
    invocation_id: str = "issue-964-invocation-1",
    idempotency_key: str = "issue-964-idempotency-1",
) -> ToolInvocation:
    task_id = new_id("task")
    run_id = new_id("run")
    agent_id = new_id("agent")
    return ToolInvocation(
        invocation_id=invocation_id,
        tool_ref="long_tool",
        arguments={"value": 1},
        context=OperationContext(
            correlation_id=task_id,
            control=OperationControl(idempotency_key=idempotency_key),
        ),
        task_id=task_id,
        run_id=run_id,
        agent_id=agent_id,
    )


def _client_capabilities(request: dict[str, JsonValue]) -> dict[str, JsonValue]:
    params = request["params"]
    assert isinstance(params, dict)
    meta = params["_meta"]
    assert isinstance(meta, dict)
    capabilities = meta["io.modelcontextprotocol/clientCapabilities"]
    assert isinstance(capabilities, dict)
    return capabilities


@pytest.mark.asyncio
async def test_task_capability_negotiation_polls_to_ordinary_tool_result() -> None:
    client = _ScriptedTasksClient(
        [
            _tasks_discovery(),
            _created_task(),
            _completed_task(),
        ]
    )
    invocation = _invocation()

    outcome = await client.call_tool_for_invocation(invocation)

    assert outcome.output == {"value": 42}
    assert [request["method"] for request in client.requests] == [
        "server/discover",
        "tools/call",
        "tasks/get",
    ]
    for request in client.requests:
        assert _client_capabilities(request)["extensions"] == {
            MCP_TASKS_EXTENSION_ID: {}
        }
    metadata = outcome.adapter_metadata[0]
    assert metadata.namespace == "mcp.task"
    assert metadata.values["external_task_id"] == "external-task-1"
    assert metadata.values["external_status"] == "completed"
    assert invocation.task_id != "external-task-1"
    assert invocation.run_id != "external-task-1"


@pytest.mark.asyncio
async def test_server_without_tasks_uses_synchronous_tool_fallback() -> None:
    client = _ScriptedTasksClient(
        [
            _tasks_discovery(supported=False),
            _result(
                {
                    "resultType": "complete",
                    "structuredContent": {"value": 7},
                    "isError": False,
                }
            ),
        ]
    )

    outcome = await client.call_tool_for_invocation(_invocation())

    assert outcome.output == {"value": 7}
    assert [request["method"] for request in client.requests] == [
        "server/discover",
        "tools/call",
    ]
    assert _client_capabilities(client.requests[0])["extensions"] == {
        MCP_TASKS_EXTENSION_ID: {}
    }
    assert _client_capabilities(client.requests[1]) == {}


@pytest.mark.asyncio
async def test_task_result_without_negotiation_is_rejected() -> None:
    client = _ScriptedTasksClient([_created_task()])

    with pytest.raises(ContractError) as exc_info:
        await client.call_tool("long_tool", {"value": 1})

    assert exc_info.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE


@pytest.mark.asyncio
async def test_unknown_external_task_state_fails_closed() -> None:
    client = _ScriptedTasksClient(
        [
            _tasks_discovery(),
            _created_task(),
            _result(
                {
                    "resultType": "complete",
                    "taskId": "external-task-1",
                    "status": "paused_by_future_extension",
                    "pollIntervalMs": 0,
                }
            ),
        ]
    )

    with pytest.raises(ContractError) as exc_info:
        await client.call_tool_for_invocation(_invocation())

    assert exc_info.value.code is ErrorCode.INVALID_PROVIDER_RESPONSE
    assert exc_info.value.details["external_task_state_unknown"] is True


@pytest.mark.asyncio
async def test_external_failed_state_is_provider_failure_not_canonical_task_state() -> None:
    client = _ScriptedTasksClient(
        [
            _tasks_discovery(),
            _created_task(),
            _result(
                {
                    "resultType": "complete",
                    "taskId": "external-task-1",
                    "status": "failed",
                    "error": {"code": -32603, "message": "provider failed"},
                }
            ),
        ]
    )

    with pytest.raises(ContractError) as exc_info:
        await client.call_tool_for_invocation(_invocation())

    assert exc_info.value.code is ErrorCode.BACKEND_ERROR
    assert exc_info.value.details == {
        "external_task_failed": True,
        "external_error_code": -32603,
    }


@pytest.mark.asyncio
async def test_disappeared_external_task_is_unavailable_not_failed_or_cancelled() -> None:
    client = _ScriptedTasksClient(
        [
            _tasks_discovery(),
            _created_task(),
            _error(-32602, "unknown task"),
        ]
    )

    with pytest.raises(ContractError) as exc_info:
        await client.call_tool_for_invocation(_invocation())

    assert exc_info.value.code is ErrorCode.UNAVAILABLE
    assert exc_info.value.retryable is True
    assert exc_info.value.details == {"external_task_state_unavailable": True}


@pytest.mark.asyncio
async def test_cancel_is_scoped_to_bound_canonical_invocation() -> None:
    repository = InMemoryMCPTaskBindingRepository()
    invocation = _invocation()
    binding = binding_for_invocation(
        invocation,
        server_id="tasks-test",
        external_task_id="external-task-1",
        external_status="working",
        external_created_at="2026-09-13T18:00:00Z",
        protocol_revision="2026-07-28",
    )
    await repository.put_if_absent(binding)
    client = _ScriptedTasksClient(
        [_result({"resultType": "complete"})],
        task_bindings=repository,
    )

    metadata = await client.cancel_invocation_task(invocation)

    assert client.requests[0]["method"] == "tasks/cancel"
    params = client.requests[0]["params"]
    assert isinstance(params, dict)
    assert params["taskId"] == "external-task-1"
    assert metadata[0].values["cancellation_outcome"] == "acknowledged"

    unrelated = replace(invocation, tool_ref="other_tool")
    with pytest.raises(ContractError) as exc_info:
        await client.cancel_invocation_task(unrelated)
    assert exc_info.value.code is ErrorCode.FORBIDDEN
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_sqlite_binding_resumes_after_client_restart_without_redispatch(
    tmp_path: Path,
) -> None:
    invocation = _invocation()
    database = tmp_path / "mcp-task-bindings.sqlite3"
    first_repository = SqliteMCPTaskBindingRepository(database)
    await first_repository.put_if_absent(
        binding_for_invocation(
            invocation,
            server_id="tasks-test",
            external_task_id="external-task-1",
            external_status="working",
            external_created_at="2026-09-13T18:00:00Z",
            protocol_revision="2026-07-28",
        )
    )

    restarted_repository = SqliteMCPTaskBindingRepository(database)
    client = _ScriptedTasksClient(
        [_completed_task()],
        task_bindings=restarted_repository,
    )

    outcome = await client.call_tool_for_invocation(invocation)

    assert outcome.output == {"value": 42}
    assert [request["method"] for request in client.requests] == ["tasks/get"]


@pytest.mark.asyncio
async def test_one_invocation_attempt_cannot_be_rebound_to_second_external_task() -> None:
    repository = InMemoryMCPTaskBindingRepository()
    invocation = _invocation()
    first = binding_for_invocation(
        invocation,
        server_id="tasks-test",
        external_task_id="external-task-1",
        external_status="working",
        external_created_at=None,
        protocol_revision="2026-07-28",
    )
    second = replace(first, external_task_id="external-task-2")

    await repository.put_if_absent(first)
    with pytest.raises(ContractError) as exc_info:
        await repository.put_if_absent(second)

    assert exc_info.value.code is ErrorCode.CONFLICT
    assert exc_info.value.details == {"mcp_task_binding_conflict": True}


@pytest.mark.asyncio
async def test_input_required_uses_scoped_tasks_update_then_resumes_polling() -> None:
    seen_inputs: list[dict[str, JsonValue]] = []

    async def input_handler(
        invocation: ToolInvocation,
        requests: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        seen_inputs.append(requests)
        return {
            "name": {
                "action": "accept",
                "content": {"input": "Samu"},
            }
        }

    client = _ScriptedTasksClient(
        [
            _tasks_discovery(),
            _created_task(),
            _result(
                {
                    "resultType": "complete",
                    "taskId": "external-task-1",
                    "status": "input_required",
                    "pollIntervalMs": 0,
                    "inputRequests": {
                        "name": {
                            "method": "elicitation/create",
                            "params": {"message": "Name?"},
                        }
                    },
                }
            ),
            _result({"resultType": "complete"}),
            _completed_task(value=9),
        ],
        task_input_handler=input_handler,
    )

    outcome = await client.call_tool_for_invocation(_invocation())

    assert outcome.output == {"value": 9}
    assert seen_inputs[0]["name"] == {
        "method": "elicitation/create",
        "params": {"message": "Name?"},
    }
    assert [request["method"] for request in client.requests] == [
        "server/discover",
        "tools/call",
        "tasks/get",
        "tasks/update",
        "tasks/get",
    ]


@pytest.mark.asyncio
async def test_capability_invoker_keeps_canonical_ids_while_mcp_task_is_external() -> None:
    task_id = new_id("task")
    run_id = new_id("run")
    agent_id = new_id("agent")
    project_id = new_id("project")
    context = OperationContext(
        correlation_id=task_id,
        project_id=project_id,
        control=OperationControl(idempotency_key="issue-964-e2e"),
    )
    request = CapabilityInvocation(
        invocation_id="issue-964-capability-1",
        capability_id="tool.long.tool",
        version="1.0",
        arguments={"value": 1},
        context=context,
        trace=InvocationTrace(
            correlation_id=task_id,
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
            project_id=project_id,
        ),
    )
    client = _ScriptedTasksClient(
        [
            _result(
                {
                    "tools": [
                        {
                            "name": "long_tool",
                            "inputSchema": {"type": "object"},
                        }
                    ]
                }
            ),
            _result({"capabilities": {}}),
            _tasks_discovery(),
            _created_task(),
            _completed_task(value=11),
        ]
    )
    provider = MCPToolProvider(
        MCPServerConfig(
            server_id="tasks-test",
            endpoint="http://localhost.invalid/mcp",
        ),
        client,
    )
    registry = CapabilityRegistry()
    await registry.register_provider(provider)

    result = await CapabilityInvoker(registry).invoke(request)

    assert result.output == {"value": 11}
    assert result.invocation_id == request.invocation_id
    assert result.status.value == "succeeded"
    task_metadata = next(
        item for item in result.adapter_metadata if item.namespace == "mcp.task"
    )
    assert task_metadata.values["external_task_id"] == "external-task-1"
    assert task_id != task_metadata.values["external_task_id"]
    assert run_id != task_metadata.values["external_task_id"]
