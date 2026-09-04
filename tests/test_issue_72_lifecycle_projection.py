from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import TypeVar, cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import ActorContext, ControlPlane, RequestContext
from ai_multi_agent_platform.control_plane.conversation_streaming import (
    subscribe_conversation_events,
)
from ai_multi_agent_platform.conversations import ConversationService, JsonConversationRepository
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

T = TypeVar("T")

ACTOR = ActorContext(
    principal_ref="user:issue-72-projection",
    owner_type="user",
    owner_id="issue-72-projection",
    actor_type="human",
)


def _run(value: Awaitable[T]) -> T:  # noqa: UP047
    return asyncio.run(value)


async def _collect(stream: AsyncIterator[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    return [item async for item in stream]


def _stack(tmp_path: Path):
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    conversations = ConversationService(JsonConversationRepository(tmp_path / "conversations.json"))
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        conversation_service=conversations,
    )
    return kernel, conversations, control_plane


def _context() -> RequestContext:
    return RequestContext(
        request_id="request-projection",
        correlation_id="correlation-projection",
        actor=ACTOR,
    )


def test_lifecycle_stream_materializes_run_artifact_and_result_references(tmp_path: Path) -> None:
    kernel, conversations, control_plane = _stack(tmp_path)
    task = _run(
        kernel.create_task(
            idempotency_key="projection:create",
            title="Projection task",
            objective="Produce canonical lifecycle references.",
            owner_type="user",
            owner_id="issue-72-projection",
        )
    )
    conversation = _run(
        conversations.create_conversation(
            title="Projection",
            owner_ref=ACTOR.principal_ref,
        )
    )
    _run(conversations.link_task(conversation_id=conversation.id, task_id=task.task_id))
    _run(kernel.ready_task(idempotency_key="projection:ready", task_id=task.task_id))
    run = _run(kernel.start_task(idempotency_key="projection:start", task_id=task.task_id))
    artifact_id = new_id("artifact")
    result_id = new_id("result")
    _run(
        kernel.attach_artifact(
            idempotency_key="projection:artifact",
            task_id=task.task_id,
            run_id=run.run_id,
            artifact_id=artifact_id,
        )
    )
    _run(
        kernel.attach_result(
            idempotency_key="projection:result",
            task_id=task.task_id,
            run_id=run.run_id,
            result_id=result_id,
        )
    )

    stream = _run(
        subscribe_conversation_events(
            control_plane,
            conversations,
            _context(),
            conversation.id,
        )
    )
    projected = _run(_collect(stream))

    by_type = {
        cast(dict[str, JsonValue], item["event"])["event_type"]: item
        for item in projected
    }
    run_refs = cast(list[dict[str, JsonValue]], by_type["run.created"]["references"])
    artifact_refs = cast(
        list[dict[str, JsonValue]], by_type["artifact.attached"]["references"]
    )
    result_refs = cast(list[dict[str, JsonValue]], by_type["result.attached"]["references"])
    assert {"kind": "run", "id": run.run_id, "label": None, "metadata": {}} in run_refs
    assert {
        "kind": "artifact",
        "id": artifact_id,
        "label": None,
        "metadata": {},
    } in artifact_refs
    assert {"kind": "result", "id": result_id, "label": None, "metadata": {}} in result_refs
    assert all(item["authoritative"] is True for item in projected)

    materialized = _run(conversations.get_conversation(conversation.id))
    assert run.run_id in materialized.run_ids
    assert artifact_id in materialized.artifact_ids


def test_waiting_task_stream_projects_structured_attention_without_resuming(tmp_path: Path) -> None:
    kernel, conversations, control_plane = _stack(tmp_path)
    task = _run(
        kernel.create_task(
            idempotency_key="waiting:create",
            title="Waiting projection",
            objective="Wait for explicit user input.",
            owner_type="user",
            owner_id="issue-72-projection",
        )
    )
    conversation = _run(
        conversations.create_conversation(
            title="Waiting projection",
            owner_ref=ACTOR.principal_ref,
        )
    )
    _run(conversations.link_task(conversation_id=conversation.id, task_id=task.task_id))
    _run(kernel.ready_task(idempotency_key="waiting:ready", task_id=task.task_id))
    _run(kernel.start_task(idempotency_key="waiting:start", task_id=task.task_id))
    _run(
        kernel.wait_task(
            idempotency_key="waiting:wait",
            task_id=task.task_id,
            reason="Need the user's explicit choice",
            blocked=True,
        )
    )

    stream = _run(
        subscribe_conversation_events(
            control_plane,
            conversations,
            _context(),
            conversation.id,
        )
    )
    projected = _run(_collect(stream))
    waiting = next(
        item
        for item in projected
        if cast(dict[str, JsonValue], item["event"])["event_type"] == "task.waiting"
    )

    assert waiting["attention"] == {
        "kind": "task_waiting",
        "task_id": task.task_id,
        "blocked": True,
        "reason": "Need the user's explicit choice",
    }
    assert (_run(kernel.get_task(task.task_id))).status.value == "waiting"
