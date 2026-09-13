from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import Any, TypeVar, cast

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    ControlPlaneASGI,
    ControlPlaneHTTP,
    HTTPRequest,
    RequestContext,
    build_openapi,
)
from ai_multi_agent_platform.control_plane.conversation_streaming import (
    subscribe_conversation_events,
)
from ai_multi_agent_platform.conversations import ConversationService, JsonConversationRepository
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

T = TypeVar("T")

ACTOR = ActorContext(
    principal_ref="user:issue-72-stream",
    owner_type="user",
    owner_id="issue-72-stream",
    actor_type="human",
)


def _run(value: Awaitable[T]) -> T:  # noqa: UP047
    return asyncio.run(value)


def _stack(tmp_path: Path):
    events = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=events,
    )
    conversations = ConversationService(JsonConversationRepository(tmp_path / "conversations.json"))
    control_plane = ControlPlane(
        kernel=kernel,
        events=events,
        conversation_service=conversations,
    )
    return kernel, conversations, control_plane


def _context() -> RequestContext:
    return RequestContext(
        request_id="request-stream",
        correlation_id="correlation-stream",
        actor=ACTOR,
    )


async def _collect(stream: AsyncIterator[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    return [item async for item in stream]


async def _task(kernel: PlatformKernel, key: str) -> str:
    state = await kernel.create_task(
        idempotency_key=f"{key}:create",
        title=f"Task {key}",
        objective="Produce canonical events for conversation streaming.",
        owner_type="user",
        owner_id="issue-72-stream",
    )
    return state.task_id


def test_conversation_stream_multiplexes_task_and_run_events_with_cursor(tmp_path: Path) -> None:
    kernel, conversations, control_plane = _stack(tmp_path)
    conversation = _run(
        conversations.create_conversation(
            title="Multiplexed stream",
            owner_ref=ACTOR.principal_ref,
        )
    )
    task_a = _run(_task(kernel, "a"))
    task_b = _run(_task(kernel, "b"))
    _run(conversations.link_task(conversation_id=conversation.id, task_id=task_a))
    _run(conversations.link_task(conversation_id=conversation.id, task_id=task_b))

    _run(kernel.ready_task(idempotency_key="a:ready", task_id=task_a))
    _run(kernel.start_task(idempotency_key="a:start", task_id=task_a))

    first = _run(
        subscribe_conversation_events(
            control_plane,
            conversations,
            _context(),
            conversation.id,
        )
    )
    projected = _run(_collect(first))

    assert {cast(str, item["task_id"]) for item in projected} == {task_a, task_b}
    assert all(item["type"] == "conversation.task-event" for item in projected)
    assert all(item["authoritative"] is True for item in projected)
    canonical_types = {
        cast(dict[str, JsonValue], item["event"])["event_type"] for item in projected
    }
    assert "task.created" in canonical_types
    assert "run.created" in canonical_types

    cursor = cast(str, projected[-1]["id"])
    _run(
        kernel.update_task(
            idempotency_key="b:metadata",
            task_id=task_b,
            metadata={"after_cursor": True},
        )
    )
    resumed = _run(
        subscribe_conversation_events(
            control_plane,
            conversations,
            _context(),
            conversation.id,
            after_event_id=cursor,
        )
    )
    resumed_items = _run(_collect(resumed))
    assert len(resumed_items) == 1
    assert resumed_items[0]["task_id"] == task_b
    resumed_event = cast(dict[str, JsonValue], resumed_items[0]["event"])
    assert resumed_event["event_type"] == "task.updated"


def test_conversation_stream_rejects_private_owner_mismatch(tmp_path: Path) -> None:
    _, conversations, control_plane = _stack(tmp_path)
    conversation = _run(
        conversations.create_conversation(
            title="Private stream",
            owner_ref="user:someone-else",
        )
    )

    with pytest.raises(ContractError) as error:
        _run(
            subscribe_conversation_events(
                control_plane,
                conversations,
                _context(),
                conversation.id,
            )
        )
    assert error.value.code is ErrorCode.FORBIDDEN


def test_conversation_asgi_emits_sse_with_opaque_reconnect_cursor(tmp_path: Path) -> None:
    kernel, conversations, control_plane = _stack(tmp_path)
    conversation = _run(
        conversations.create_conversation(
            title="SSE stream",
            owner_ref="local:anonymous",
        )
    )
    task_id = _run(_task(kernel, "sse"))
    _run(conversations.link_task(conversation_id=conversation.id, task_id=task_id))

    app = ControlPlaneASGI(ControlPlaneHTTP(control_plane))
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    _run(
        app(
            {
                "type": "http",
                "method": "GET",
                "path": f"/api/v1/conversations/{conversation.id}/events/stream",
                "headers": [],
                "query_string": b"",
            },
            receive,
            send,
        )
    )

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 200
    frames = [
        bytes(item.get("body", b"")).decode("utf-8")
        for item in sent
        if item.get("type") == "http.response.body" and item.get("body")
    ]
    assert frames
    first_frame = frames[0]
    assert first_frame.startswith("id: ")
    assert "event: conversation.task-event\n" in first_frame
    data_line = next(line for line in first_frame.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["conversation_id"] == conversation.id
    assert payload["task_id"] == task_id
    assert payload["authoritative"] is True
    assert payload["event"]["event_type"] == "task.created"

    cursor = first_frame.splitlines()[0].removeprefix("id: ")
    assert cursor
    assert task_id not in cursor


def test_conversation_stream_rejects_invalid_cursor(tmp_path: Path) -> None:
    _, conversations, control_plane = _stack(tmp_path)
    conversation = _run(
        conversations.create_conversation(
            title="Bad cursor",
            owner_ref=ACTOR.principal_ref,
        )
    )

    with pytest.raises(ContractError) as error:
        _run(
            subscribe_conversation_events(
                control_plane,
                conversations,
                _context(),
                conversation.id,
                after_event_id="not-a-valid-cursor",
            )
        )
    assert error.value.code is ErrorCode.INVALID_REQUEST


def test_conversation_http_requires_sse_transport(tmp_path: Path) -> None:
    _, conversations, control_plane = _stack(tmp_path)
    conversation = _run(
        conversations.create_conversation(
            title="HTTP stream contract",
            owner_ref=ACTOR.principal_ref,
        )
    )
    response = _run(
        ControlPlaneHTTP(control_plane).handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/conversations/{conversation.id}/events/stream",
            )
        )
    )

    assert response.status == 406
    assert isinstance(response.body, dict)
    assert response.body["code"] == "stream_transport_required"


def test_conversation_openapi_documents_provider_neutral_event_stream() -> None:
    specification = build_openapi(include_conversations=True)
    path = "/api/v1/conversations/{conversation_id}/events/stream"

    assert path in specification["paths"]
    assert specification["paths"][path]["get"]["operationId"] == "streamConversationEvents"
    assert specification["x-conversation-event-stream-provider-neutral"] is True
