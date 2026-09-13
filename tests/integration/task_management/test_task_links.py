from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar, cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
)
from ai_multi_agent_platform.conversations import (
    ConversationContentBlock,
    ConversationService,
    JsonConversationRepository,
    MessageRole,
    ReferenceKind,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

T = TypeVar("T")

ACTOR = ActorContext(
    principal_ref="user:issue-72-links",
    owner_type="user",
    owner_id="issue-72-links",
    actor_type="human",
)


def _run(value: Awaitable[T]) -> T:  # noqa: UP047
    return asyncio.run(value)


def _http(tmp_path: Path) -> tuple[ControlPlaneHTTP, ConversationService]:
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
    return ControlPlaneHTTP(control_plane), conversations


def _post(
    http: ControlPlaneHTTP,
    path: str,
    body: dict[str, JsonValue],
    *,
    key: str,
) -> dict[str, JsonValue]:
    response = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path=path,
                headers={"content-type": "application/json", "idempotency-key": key},
                body=body,
                trusted_actor=ACTOR,
            )
        )
    )
    assert response.status in {200, 201}, response.body
    return cast(dict[str, JsonValue], response.body)


def _get(http: ControlPlaneHTTP, path: str) -> dict[str, JsonValue]:
    response = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path=path,
                trusted_actor=ACTOR,
            )
        )
    )
    assert response.status == 200, response.body
    return cast(dict[str, JsonValue], response.body)


def _project_and_task(http: ControlPlaneHTTP) -> tuple[str, str]:
    project = _post(
        http,
        "/api/v1/projects",
        {"name": "Issue 72 Task Links"},
        key="links-project",
    )
    project_id = cast(str, project["id"])
    task = _post(
        http,
        "/api/v1/tasks",
        {
            "title": "Linked Task",
            "objective": "Stay canonical while chat references this Task.",
            "project_id": project_id,
        },
        key="links-task",
    )
    return project_id, cast(str, task["id"])


def test_task_target_is_recorded_in_conversation_task_ids(tmp_path: Path) -> None:
    http, _ = _http(tmp_path)
    project_id, task_id = _project_and_task(http)

    conversation = _post(
        http,
        "/api/v1/conversations",
        {
            "title": "Task-scoped chat",
            "project_id": project_id,
            "target": {"kind": "task", "id": task_id},
        },
        key="task-target-conversation",
    )

    assert conversation["task_ids"] == [task_id]
    persisted = _get(http, f"/api/v1/conversations/{conversation['id']}")
    assert persisted["task_ids"] == [task_id]


def test_attach_task_updates_message_and_conversation_task_links(tmp_path: Path) -> None:
    http, _ = _http(tmp_path)
    project_id, task_id = _project_and_task(http)
    conversation = _post(
        http,
        "/api/v1/conversations",
        {"title": "Attach existing Task", "project_id": project_id},
        key="attach-conversation",
    )
    conversation_id = cast(str, conversation["id"])
    message = _post(
        http,
        f"/api/v1/conversations/{conversation_id}/messages",
        {"content": [{"kind": "text", "text": "Attach this to existing work."}]},
        key="attach-message",
    )
    message_id = cast(str, message["id"])

    attached = _post(
        http,
        f"/api/v1/conversation-messages/{message_id}:attach-task",
        {"task_id": task_id},
        key="attach-task",
    )
    assert attached["conversation_id"] == conversation_id

    persisted = _get(http, f"/api/v1/conversations/{conversation_id}")
    assert persisted["task_ids"] == [task_id]
    message_resource = _get(http, f"/api/v1/conversation-messages/{message_id}")
    assert any(
        reference["kind"] == "task" and reference["id"] == task_id
        for reference in message_resource["references"]
    )


def test_service_task_link_is_deduplicated_and_restart_safe(tmp_path: Path) -> None:
    path = tmp_path / "conversation-links.json"
    first = ConversationService(JsonConversationRepository(path))
    conversation = _run(
        first.create_conversation(
            title="Durable link",
            owner_ref=ACTOR.principal_ref,
        )
    )
    message = _run(
        first.append_message(
            conversation_id=conversation.id,
            sender_ref=ACTOR.principal_ref,
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("Link once."),),
        )
    )
    task_id = new_id("task")

    _run(first.link_task(conversation_id=conversation.id, task_id=task_id, message_id=message.id))
    _run(first.link_task(conversation_id=conversation.id, task_id=task_id, message_id=message.id))

    recreated = ConversationService(JsonConversationRepository(path))
    restored = _run(recreated.get_conversation(conversation.id))
    restored_message = _run(recreated.get_message(message.id))
    assert restored.task_ids == (task_id,)
    task_references = [
        reference
        for reference in restored_message.references
        if reference.kind is ReferenceKind.TASK and reference.id == task_id
    ]
    assert len(task_references) == 1
