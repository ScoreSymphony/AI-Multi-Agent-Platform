from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar, cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    build_openapi,
)
from ai_multi_agent_platform.conversations import (
    RETENTION_METADATA_KEY,
    ConversationRetentionManager,
    ConversationRetentionMode,
    ConversationRetentionPolicy,
    ConversationService,
    ConversationStatus,
    JsonConversationRepository,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

T = TypeVar("T")

ACTOR = ActorContext(
    principal_ref="user:issue-72-retention",
    owner_type="user",
    owner_id="issue-72-retention",
    actor_type="human",
)


def _run(value: Awaitable[T]) -> T:  # noqa: UP047
    return asyncio.run(value)


def _stack(tmp_path: Path) -> tuple[ControlPlaneHTTP, ConversationService]:
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
    response = _run(http.handle(HTTPRequest(method="GET", path=path, trusted_actor=ACTOR)))
    assert response.status == 200, response.body
    return cast(dict[str, JsonValue], response.body)


def _delete(http: ControlPlaneHTTP, path: str, *, key: str) -> dict[str, JsonValue]:
    response = _run(
        http.handle(
            HTTPRequest(
                method="DELETE",
                path=path,
                headers={"content-type": "application/json", "idempotency-key": key},
                trusted_actor=ACTOR,
            )
        )
    )
    assert response.status == 200, response.body
    return cast(dict[str, JsonValue], response.body)


def _project_task_and_conversation(http: ControlPlaneHTTP) -> tuple[str, str, str, str]:
    project = _post(
        http,
        "/api/v1/projects",
        {"name": "Issue 72 Retention"},
        key="retention-project",
    )
    project_id = cast(str, project["id"])
    task = _post(
        http,
        "/api/v1/tasks",
        {
            "title": "Canonical retained Task",
            "objective": "Remain intact when chat is deleted.",
            "project_id": project_id,
        },
        key="retention-task",
    )
    task_id = cast(str, task["id"])
    conversation = _post(
        http,
        "/api/v1/conversations",
        {
            "title": "Sensitive chat title",
            "summary": "Sensitive chat summary",
            "project_id": project_id,
            "target": {"kind": "task", "id": task_id},
            "metadata": {"client_note": "delete me"},
        },
        key="retention-conversation",
    )
    conversation_id = cast(str, conversation["id"])
    message = _post(
        http,
        f"/api/v1/conversations/{conversation_id}/messages",
        {
            "content": [{"kind": "text", "text": "sensitive conversation text"}],
            "metadata": {"client_note": "sensitive message metadata"},
        },
        key="retention-message",
    )
    return project_id, task_id, conversation_id, cast(str, message["id"])


def test_delete_tombstones_chat_but_preserves_canonical_task(tmp_path: Path) -> None:
    http, _ = _stack(tmp_path)
    _, task_id, conversation_id, message_id = _project_task_and_conversation(http)
    task_before = _get(http, f"/api/v1/tasks/{task_id}")

    deleted = _delete(
        http,
        f"/api/v1/conversations/{conversation_id}",
        key="delete-conversation",
    )

    assert deleted["status"] == "tombstoned"
    assert deleted["title"] == "Deleted conversation"
    assert deleted["summary"] is None
    assert deleted["task_ids"] == [task_id]
    task_after = _get(http, f"/api/v1/tasks/{task_id}")
    assert task_after == task_before

    message = _get(http, f"/api/v1/conversation-messages/{message_id}")
    assert message["status"] == "tombstoned"
    assert message["content"] == [{"kind": "json", "value": {"tombstoned": True}}]
    assert message["references"] == []
    assert message["model_config_id"] is None
    assert message["model_provider_ref"] is None
    assert message["metadata"] == {}
    assert "sensitive conversation text" not in str(message)


def test_export_never_expands_external_resources_or_memory(tmp_path: Path) -> None:
    http, _ = _stack(tmp_path)
    _, task_id, conversation_id, _ = _project_task_and_conversation(http)

    exported = _get(http, f"/api/v1/conversations/{conversation_id}/export")

    assert exported["type"] == "conversation-export"
    assert exported["id"] == conversation_id
    external = cast(dict[str, JsonValue], exported["external_resources"])
    assert external["expanded"] is False
    assert external["task_ids"] == [task_id]
    assert external["file_content_included"] is False
    assert external["knowledge_content_included"] is False
    assert external["canonical_event_history_included"] is False
    memory = cast(dict[str, JsonValue], exported["memory"])
    assert memory == {"included": False, "automatic_promotion": False}


def test_export_after_delete_contains_only_redacted_chat_content(tmp_path: Path) -> None:
    http, _ = _stack(tmp_path)
    _, _, conversation_id, _ = _project_task_and_conversation(http)
    _delete(http, f"/api/v1/conversations/{conversation_id}", key="delete-before-export")

    exported = _get(http, f"/api/v1/conversations/{conversation_id}/export")

    serialized = str(exported)
    assert "Sensitive chat title" not in serialized
    assert "Sensitive chat summary" not in serialized
    assert "sensitive conversation text" not in serialized
    assert "delete me" not in serialized
    assert "sensitive message metadata" not in serialized
    assert "Deleted conversation" in serialized
    assert "tombstoned" in serialized


def test_until_retention_expires_open_and_archived_conversations(tmp_path: Path) -> None:
    http, conversations = _stack(tmp_path)
    conversation = _post(
        http,
        "/api/v1/conversations",
        {"title": "Expiring conversation"},
        key="expiring-conversation",
    )
    conversation_id = cast(str, conversation["id"])
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    _post(
        http,
        f"/api/v1/conversations/{conversation_id}:set-retention",
        {"mode": "until", "expires_at": expires_at.isoformat()},
        key="set-expiry",
    )
    _post(
        http,
        f"/api/v1/conversations/{conversation_id}:archive",
        {},
        key="archive-expiring",
    )

    expired = _run(
        ConversationRetentionManager(conversations).expire_due(
            now=expires_at + timedelta(seconds=1)
        )
    )

    assert expired == (conversation_id,)
    restored = _run(conversations.get_conversation(conversation_id))
    assert restored.status is ConversationStatus.TOMBSTONED


def test_durable_retention_does_not_expire(tmp_path: Path) -> None:
    _, conversations = _stack(tmp_path)
    conversation = _run(
        conversations.create_conversation(
            title="Durable conversation",
            owner_ref=ACTOR.principal_ref,
        )
    )
    manager = ConversationRetentionManager(conversations)
    updated = _run(
        manager.set_policy(
            conversation.id,
            ConversationRetentionPolicy(mode=ConversationRetentionMode.DURABLE),
        )
    )

    assert RETENTION_METADATA_KEY in updated.metadata
    expired = _run(manager.expire_due(now=datetime.now(UTC) + timedelta(days=3650)))
    assert expired == ()
    assert _run(conversations.get_conversation(conversation.id)).status is ConversationStatus.OPEN


def test_clients_cannot_inject_platform_retention_metadata(tmp_path: Path) -> None:
    http, _ = _stack(tmp_path)
    response = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/conversations",
                headers={
                    "content-type": "application/json",
                    "idempotency-key": "inject-retention",
                },
                body={
                    "title": "Injected retention",
                    "metadata": {RETENTION_METADATA_KEY: {"mode": "durable", "expires_at": None}},
                },
                trusted_actor=ACTOR,
            )
        )
    )
    assert response.status == 400


def test_retention_routes_are_documented_without_memory_or_event_conflation() -> None:
    specification = build_openapi(include_conversations=True)
    paths = cast(dict[str, JsonValue], specification["paths"])

    assert "/api/v1/conversations/{conversation_id}:set-retention" in paths
    assert "/api/v1/conversations/{conversation_id}/export" in paths
    conversation_path = cast(dict[str, JsonValue], paths["/api/v1/conversations/{conversation_id}"])
    assert "delete" in conversation_path
