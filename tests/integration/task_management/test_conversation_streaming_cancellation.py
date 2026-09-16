from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.control_plane.conversation_streaming import (
    _pump_task_events,
    subscribe_conversation_events,
)
from ai_multi_agent_platform.conversations import ConversationService, JsonConversationRepository
from ai_multi_agent_platform.domain import new_id

_ACTOR = ActorContext(
    principal_ref="user:conversation-cancellation",
    owner_type="user",
    owner_id="conversation-cancellation",
    actor_type="human",
)
_PUMP_PREFIX = "conversation-stream-pump:"
_StreamFactory = Callable[[], AsyncIterator[dict[str, JsonValue]]]


class _StreamControlPlane:
    def __init__(self, streams: dict[str, _StreamFactory]) -> None:
        self._streams = streams

    async def _authorize(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def subscribe_task_events(
        self,
        context: RequestContext,
        task_id: str,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, JsonValue]]:
        del context, after_event_id
        return self._streams[task_id]()


def _context() -> RequestContext:
    return RequestContext(
        request_id="request-conversation-cancellation",
        correlation_id="correlation-conversation-cancellation",
        actor=_ACTOR,
    )


async def _stream(
    tmp_path: Path,
    streams: dict[str, _StreamFactory],
) -> AsyncIterator[dict[str, JsonValue]]:
    service = ConversationService(JsonConversationRepository(tmp_path / "conversations.json"))
    conversation = await service.create_conversation(
        title="Cancellation semantics",
        owner_ref=_ACTOR.principal_ref,
    )
    for task_id in streams:
        await service.link_task(conversation_id=conversation.id, task_id=task_id)
    return await subscribe_conversation_events(
        _StreamControlPlane(streams),
        service,
        _context(),
        conversation.id,
    )


async def _assert_no_pumps() -> None:
    await asyncio.sleep(0)
    current = asyncio.current_task()
    leaked = [
        task
        for task in asyncio.all_tasks()
        if task is not current and task.get_name().startswith(_PUMP_PREFIX)
    ]
    assert leaked == []


@pytest.mark.asyncio
async def test_child_ordinary_exception_reaches_aggregator_unchanged(tmp_path: Path) -> None:
    task_id = new_id("task")
    failure = RuntimeError("synthetic child stream failure")

    async def failing() -> AsyncIterator[dict[str, JsonValue]]:
        raise failure
        yield {}  # pragma: no cover - keeps this an async generator

    stream = await _stream(tmp_path, {task_id: failing})
    with pytest.raises(RuntimeError) as exc_info:
        await anext(stream)

    assert exc_info.value is failure
    await _assert_no_pumps()


@pytest.mark.asyncio
async def test_child_contract_error_preserves_code_and_cause(tmp_path: Path) -> None:
    task_id = new_id("task")
    cause = ValueError("synthetic cause")

    async def failing() -> AsyncIterator[dict[str, JsonValue]]:
        try:
            raise cause
        except ValueError as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "synthetic contract failure") from exc
        yield {}  # pragma: no cover - keeps this an async generator

    stream = await _stream(tmp_path, {task_id: failing})
    with pytest.raises(ContractError) as exc_info:
        await anext(stream)

    assert exc_info.value.code is ErrorCode.BACKEND_ERROR
    assert exc_info.value.__cause__ is cause
    await _assert_no_pumps()


@pytest.mark.asyncio
async def test_independently_cancelled_pump_cancels_aggregator(tmp_path: Path) -> None:
    task_id = new_id("task")

    async def cancelling() -> AsyncIterator[dict[str, JsonValue]]:
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        await asyncio.sleep(0)
        yield {}  # pragma: no cover - cancellation exits first

    stream = await _stream(tmp_path, {task_id: cancelling})
    with pytest.raises(asyncio.CancelledError):
        await anext(stream)

    await _assert_no_pumps()


@pytest.mark.asyncio
@pytest.mark.parametrize("signal_type", [KeyboardInterrupt, SystemExit])
async def test_pump_does_not_contain_process_signal(
    signal_type: type[BaseException],
) -> None:
    task_id = new_id("task")

    async def signalling() -> AsyncIterator[dict[str, JsonValue]]:
        raise signal_type("synthetic process signal")
        yield {}  # pragma: no cover - keeps this an async generator

    queue: asyncio.Queue[tuple[str, dict[str, JsonValue] | None, BaseException | None]] = (
        asyncio.Queue()
    )
    control_plane = _StreamControlPlane({task_id: signalling})

    with pytest.raises(signal_type):
        await _pump_task_events(control_plane, _context(), task_id, None, queue)

    assert queue.get_nowait() == (task_id, None, None)
    assert queue.empty()


@pytest.mark.asyncio
async def test_failing_pump_cancels_and_drains_other_active_pumps(tmp_path: Path) -> None:
    active_task_id = new_id("task")
    failing_task_id = new_id("task")
    active_started = asyncio.Event()
    active_stopped = asyncio.Event()
    blocker = asyncio.Event()

    async def active() -> AsyncIterator[dict[str, JsonValue]]:
        active_started.set()
        try:
            await blocker.wait()
        finally:
            active_stopped.set()
        yield {}  # pragma: no cover - teardown cancels first

    async def failing() -> AsyncIterator[dict[str, JsonValue]]:
        await active_started.wait()
        raise RuntimeError("one pump failed")
        yield {}  # pragma: no cover - keeps this an async generator

    stream = await _stream(
        tmp_path,
        {
            active_task_id: active,
            failing_task_id: failing,
        },
    )
    with pytest.raises(RuntimeError, match="one pump failed"):
        await asyncio.wait_for(anext(stream), timeout=1.0)

    await asyncio.wait_for(active_stopped.wait(), timeout=1.0)
    await _assert_no_pumps()


@pytest.mark.asyncio
async def test_repeated_consumer_cancellation_drains_pumps_without_deadlock(tmp_path: Path) -> None:
    task_id = new_id("task")
    active_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()
    active_stopped = asyncio.Event()
    blocker = asyncio.Event()

    async def active() -> AsyncIterator[dict[str, JsonValue]]:
        active_started.set()
        try:
            await blocker.wait()
        finally:
            cleanup_started.set()
            await allow_cleanup.wait()
            active_stopped.set()
        yield {}  # pragma: no cover - teardown cancels first

    stream = await _stream(tmp_path, {task_id: active})
    consumer = asyncio.create_task(anext(stream))
    await asyncio.wait_for(active_started.wait(), timeout=1.0)

    consumer.cancel()
    await asyncio.wait_for(cleanup_started.wait(), timeout=1.0)
    consumer.cancel()
    allow_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(consumer, timeout=1.0)

    assert active_stopped.is_set()
    await _assert_no_pumps()
