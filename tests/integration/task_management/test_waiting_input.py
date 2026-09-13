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
from ai_multi_agent_platform.conversations import ConversationService, JsonConversationRepository
from ai_multi_agent_platform.domain import TaskStatus
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

T = TypeVar("T")

ACTOR = ActorContext(
    principal_ref="user:issue-72-waiting",
    owner_type="user",
    owner_id="issue-72-waiting",
    actor_type="human",
)


def _run(value: Awaitable[T]) -> T:  # noqa: UP047
    return asyncio.run(value)


def _stack(tmp_path: Path) -> tuple[ControlPlaneHTTP, PlatformKernel, ConversationService]:
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
    return ControlPlaneHTTP(control_plane), kernel, conversations


def _request(
    http: ControlPlaneHTTP,
    method: str,
    path: str,
    *,
    body: dict[str, JsonValue] | None = None,
    key: str | None = None,
):
    headers = {"content-type": "application/json"}
    if key is not None:
        headers["idempotency-key"] = key
    return _run(
        http.handle(
            HTTPRequest(
                method=method,
                path=path,
                headers=headers,
                body=body or {},
                trusted_actor=ACTOR,
            )
        )
    )


def _post(
    http: ControlPlaneHTTP,
    path: str,
    body: dict[str, JsonValue],
    *,
    key: str,
) -> dict[str, JsonValue]:
    response = _request(http, "POST", path, body=body, key=key)
    assert response.status in {200, 201}, response.body
    return cast(dict[str, JsonValue], response.body)


def _task(http: ControlPlaneHTTP, *, project_id: str | None = None, key: str) -> str:
    payload: dict[str, JsonValue] = {
        "title": "Waiting Task",
        "objective": "Wait for explicit human input.",
    }
    if project_id is not None:
        payload["project_id"] = project_id
    task = _post(http, "/api/v1/tasks", payload, key=key)
    return cast(str, task["id"])


def _wait(kernel: PlatformKernel, task_id: str, *, key: str) -> None:
    _run(kernel.ready_task(idempotency_key=f"{key}:ready", task_id=task_id))
    _run(kernel.start_task(idempotency_key=f"{key}:start", task_id=task_id))
    waiting = _run(
        kernel.wait_task(
            idempotency_key=f"{key}:wait",
            task_id=task_id,
            reason="Need explicit user input",
            blocked=True,
        )
    )
    assert waiting.status is TaskStatus.WAITING


def _conversation_message(http: ControlPlaneHTTP, task_id: str, *, key: str) -> tuple[str, str]:
    conversation = _post(
        http,
        "/api/v1/conversations",
        {"title": "Waiting input", "target": {"kind": "task", "id": task_id}},
        key=f"{key}:conversation",
    )
    conversation_id = cast(str, conversation["id"])
    message = _post(
        http,
        f"/api/v1/conversations/{conversation_id}/messages",
        {"content": [{"kind": "text", "text": "Use option B and continue."}]},
        key=f"{key}:message",
    )
    return conversation_id, cast(str, message["id"])


def test_plain_message_never_resumes_waiting_task(tmp_path: Path) -> None:
    http, kernel, _ = _stack(tmp_path)
    task_id = _task(http, key="plain-task")
    _wait(kernel, task_id, key="plain")

    _conversation_message(http, task_id, key="plain")

    state = _run(kernel.get_task(task_id))
    assert state.status is TaskStatus.WAITING
    history = _run(kernel.history(task_id))
    assert not any(event.event_type == "task.resumed" for event in history)


def test_explicit_resume_uses_message_reference_and_kernel_transition(tmp_path: Path) -> None:
    http, kernel, conversations = _stack(tmp_path)
    task_id = _task(http, key="resume-task")
    _wait(kernel, task_id, key="resume")
    conversation_id, message_id = _conversation_message(http, task_id, key="resume")

    result = _post(
        http,
        f"/api/v1/conversation-messages/{message_id}:resume-task",
        {"task_id": task_id},
        key="resume-input",
    )

    task = cast(dict[str, JsonValue], result["task"])
    assert task["status"] == "running"
    metadata = cast(dict[str, JsonValue], task["metadata"])
    assert metadata["conversation_input"] == {
        "conversation_id": conversation_id,
        "message_id": message_id,
    }
    assert "Use option B" not in repr(metadata)

    conversation = _run(conversations.get_conversation(conversation_id))
    message = _run(conversations.get_message(message_id))
    assert task_id in conversation.task_ids
    assert any(
        reference.kind.value == "task" and reference.id == task_id
        for reference in message.references
    )

    history = _run(kernel.history(task_id))
    assert sum(event.event_type == "task.resumed" for event in history) == 1
    assert any(
        event.event_type == "task.updated"
        and event.provenance is not None
        and event.provenance.source == "control-plane:conversation"
        for event in history
    )

    replay = _post(
        http,
        f"/api/v1/conversation-messages/{message_id}:resume-task",
        {"task_id": task_id},
        key="resume-input",
    )
    replay_task = cast(dict[str, JsonValue], replay["task"])
    assert replay_task["status"] == "running"
    replay_history = _run(kernel.history(task_id))
    assert sum(event.event_type == "task.resumed" for event in replay_history) == 1


def test_resume_rejects_task_that_was_never_waiting(tmp_path: Path) -> None:
    http, kernel, _ = _stack(tmp_path)
    task_id = _task(http, key="draft-task")
    _, message_id = _conversation_message(http, task_id, key="draft")

    response = _request(
        http,
        "POST",
        f"/api/v1/conversation-messages/{message_id}:resume-task",
        body={"task_id": task_id},
        key="draft-resume",
    )

    assert response.status == 409
    state = _run(kernel.get_task(task_id))
    assert state.status is TaskStatus.DRAFT
    history = _run(kernel.history(task_id))
    assert not any(event.event_type == "task.resumed" for event in history)
    assert "conversation_input" not in state.task.metadata


def test_resume_rejects_cross_project_task_before_lifecycle_change(tmp_path: Path) -> None:
    http, kernel, conversations = _stack(tmp_path)
    project_a = _post(http, "/api/v1/projects", {"name": "Project A"}, key="project-a")
    project_b = _post(http, "/api/v1/projects", {"name": "Project B"}, key="project-b")
    task_id = _task(http, project_id=cast(str, project_b["id"]), key="project-b-task")
    _wait(kernel, task_id, key="cross")

    conversation = _post(
        http,
        "/api/v1/conversations",
        {"title": "Project A chat", "project_id": cast(str, project_a["id"])},
        key="project-a-conversation",
    )
    conversation_id = cast(str, conversation["id"])
    message = _post(
        http,
        f"/api/v1/conversations/{conversation_id}/messages",
        {"content": [{"kind": "text", "text": "Do not cross projects."}]},
        key="project-a-message",
    )
    message_id = cast(str, message["id"])

    response = _request(
        http,
        "POST",
        f"/api/v1/conversation-messages/{message_id}:resume-task",
        body={"task_id": task_id},
        key="cross-resume",
    )

    assert response.status == 403
    assert _run(kernel.get_task(task_id)).status is TaskStatus.WAITING
    assert task_id not in _run(conversations.get_conversation(conversation_id)).task_ids
