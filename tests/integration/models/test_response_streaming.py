from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import Any, TypeVar, cast

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
from ai_multi_agent_platform.control_plane.conversation_response_streaming import (
    stream_conversation_response,
)
from ai_multi_agent_platform.conversations import (
    ConversationContentBlock,
    ConversationResponseChunk,
    ConversationResponseChunkKind,
    ConversationResponseRequest,
    ConversationService,
    JsonConversationRepository,
    MessageRole,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

T = TypeVar("T")

ACTOR = ActorContext(
    principal_ref="user:issue-72-response",
    owner_type="user",
    owner_id="issue-72-response",
    actor_type="human",
)
AGENT_ID = "agent_c96def53-54a7-5a11-982c-6f2a615b2fdb"


def _run(value: Awaitable[T]) -> T:  # noqa: UP047
    return asyncio.run(value)


class _ResponseProvider:
    def __init__(self, parts: tuple[str, ...]) -> None:
        self.parts = parts
        self.requests: list[ConversationResponseRequest] = []
        self.private_session_id = "provider-private-session-must-never-leak"

    def stream_response(
        self,
        request: ConversationResponseRequest,
    ) -> AsyncIterator[ConversationResponseChunk]:
        self.requests.append(request)

        async def stream() -> AsyncIterator[ConversationResponseChunk]:
            for index, part in enumerate(self.parts):
                if index == 1:
                    yield ConversationResponseChunk(
                        ConversationResponseChunkKind.ACTIVITY,
                        "Reasoning summary allowed by policy",
                    )
                yield ConversationResponseChunk(
                    ConversationResponseChunkKind.TEXT,
                    part,
                    model_config_id="model-config-canonical",
                )

        return stream()


def _stack(tmp_path: Path, provider: _ResponseProvider | None = None):
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
        conversation_response_provider=provider,
    )
    return conversations, control_plane


def _context(key: str = "response-key") -> RequestContext:
    return RequestContext(
        request_id=f"request-{key}",
        correlation_id=f"correlation-{key}",
        idempotency_key=key,
        actor=ACTOR,
    )


async def _collect(stream: AsyncIterator[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    return [item async for item in stream]


async def _conversation_with_user_message(
    conversations: ConversationService,
    *,
    text: str = "Please answer this conversationally.",
    owner_ref: str = ACTOR.principal_ref,
):
    conversation = await conversations.create_conversation(
        title="Agent response stream",
        owner_ref=owner_ref,
        metadata={
            "target": {
                "kind": "agent",
                "id": AGENT_ID,
                "revision": 3,
            }
        },
    )
    message = await conversations.append_message(
        conversation_id=conversation.id,
        sender_ref=owner_ref,
        role=MessageRole.USER,
        content=(ConversationContentBlock.text_block(text),),
    )
    return conversation, message


def test_response_stream_marks_deltas_tentative_and_commits_one_assistant_message(
    tmp_path: Path,
) -> None:
    provider = _ResponseProvider(("Hello ", "world"))
    conversations, control_plane = _stack(tmp_path, provider)
    conversation, source = _run(_conversation_with_user_message(conversations))

    stream = _run(
        stream_conversation_response(
            control_plane,
            conversations,
            provider,
            _context(),
            source.id,
        )
    )
    items = _run(_collect(stream))

    deltas = [item for item in items if item["type"] == "conversation.response.delta"]
    activity = [item for item in items if item["type"] == "conversation.response.activity"]
    committed = [item for item in items if item["type"] == "conversation.response.committed"]
    assert [cast(dict[str, JsonValue], item["delta"])["text"] for item in deltas] == [
        "Hello ",
        "world",
    ]
    assert activity
    assert all(item["tentative"] is True for item in [*deltas, *activity])
    assert all(item["authoritative"] is False for item in [*deltas, *activity])
    assert len(committed) == 1
    assert committed[0]["tentative"] is False
    assert committed[0]["authoritative"] is False
    assert committed[0]["durable"] is True

    history, cursor = _run(conversations.list_messages(conversation.id, limit=20))
    assert cursor is None
    assert len(history) == 2
    assistant = history[-1]
    assert assistant.role is MessageRole.ASSISTANT
    assert assistant.sender_ref == f"agent:{AGENT_ID}"
    assert assistant.model_config_id == "model-config-canonical"
    assert assistant.content[0].text == "Hello world"
    assert assistant.metadata["response_to"] == source.id
    assert "response_provider" not in assistant.metadata

    request = provider.requests[0]
    assert request.target.kind == "agent"
    assert request.target.id == AGENT_ID
    assert request.target.revision == 3
    serialized = json.dumps(
        {
            "target": request.target.to_json(),
            "conversation_id": request.conversation_id,
            "source_message_id": request.source_message_id,
            "events": items,
        }
    )
    assert provider.private_session_id not in serialized
    assert "session_id" not in serialized
    assert "provider_session" not in serialized


def test_response_stream_replays_committed_message_without_invoking_provider_twice(
    tmp_path: Path,
) -> None:
    provider = _ResponseProvider(("Idempotent response",))
    conversations, control_plane = _stack(tmp_path, provider)
    conversation, source = _run(_conversation_with_user_message(conversations))
    context = _context("same-response-key")

    first = _run(
        stream_conversation_response(
            control_plane,
            conversations,
            provider,
            context,
            source.id,
        )
    )
    _run(_collect(first))
    second = _run(
        stream_conversation_response(
            control_plane,
            conversations,
            provider,
            context,
            source.id,
        )
    )
    replayed = _run(_collect(second))

    assert len(provider.requests) == 1
    assert len(replayed) == 1
    assert replayed[0]["type"] == "conversation.response.committed"
    assert replayed[0]["replayed"] is True
    history, _ = _run(conversations.list_messages(conversation.id, limit=20))
    assert [message.role for message in history] == [MessageRole.USER, MessageRole.ASSISTANT]


def test_response_provider_is_replaceable_without_changing_conversation_surface(
    tmp_path: Path,
) -> None:
    first_provider = _ResponseProvider(("First backend",))
    conversations, control_plane = _stack(tmp_path, first_provider)
    conversation, source = _run(_conversation_with_user_message(conversations, text="First turn"))
    first_stream = _run(
        stream_conversation_response(
            control_plane,
            conversations,
            first_provider,
            _context("first-backend"),
            source.id,
        )
    )
    first_events = _run(_collect(first_stream))

    second_source = _run(
        conversations.append_message(
            conversation_id=conversation.id,
            sender_ref=ACTOR.principal_ref,
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("Second turn"),),
        )
    )
    replacement = _ResponseProvider(("Second backend",))
    second_stream = _run(
        stream_conversation_response(
            control_plane,
            conversations,
            replacement,
            _context("second-backend"),
            second_source.id,
        )
    )
    second_events = _run(_collect(second_stream))

    assert [item["type"] for item in first_events] == [
        "conversation.response.delta",
        "conversation.response.committed",
    ]
    assert [item["type"] for item in second_events] == [
        "conversation.response.delta",
        "conversation.response.committed",
    ]
    assert first_provider.requests[0].target == replacement.requests[0].target


def test_response_stream_asgi_emits_post_sse_and_persists_final_message(tmp_path: Path) -> None:
    provider = _ResponseProvider(("Streamed ", "assistant"))
    conversations, control_plane = _stack(tmp_path, provider)
    conversation, source = _run(
        _conversation_with_user_message(
            conversations,
            text="Use the transport",
            owner_ref="local:anonymous",
        )
    )
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
                "method": "POST",
                "path": f"/api/v1/conversation-messages/{source.id}/response/stream",
                "headers": [(b"idempotency-key", b"asgi-response-key")],
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
    joined = "".join(frames)
    assert "event: conversation.response.delta\n" in joined
    assert "event: conversation.response.committed\n" in joined
    assert '"authoritative":false' in joined
    assert provider.private_session_id not in joined

    history, _ = _run(conversations.list_messages(conversation.id, limit=20))
    assert history[-1].content[0].text == "Streamed assistant"


def test_response_stream_requires_sse_transport_and_is_documented(tmp_path: Path) -> None:
    conversations, control_plane = _stack(tmp_path, _ResponseProvider(("ok",)))
    _, source = _run(_conversation_with_user_message(conversations))
    response = _run(
        ControlPlaneHTTP(control_plane).handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/conversation-messages/{source.id}/response/stream",
            )
        )
    )
    assert response.status == 406
    assert isinstance(response.body, dict)
    assert response.body["code"] == "stream_transport_required"

    specification = build_openapi(include_conversations=True)
    path = "/api/v1/conversation-messages/{message_id}/response/stream"
    assert specification["paths"][path]["post"]["operationId"] == "streamConversationResponse"
    assert specification["x-conversation-response-provider-neutral"] is True
    assert specification["x-conversation-response-deltas-authoritative"] is False
