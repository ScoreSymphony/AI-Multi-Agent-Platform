"""Replaceable canonical persistence boundaries for the task/run/event kernel."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, PlatformEvent

from .models import RunState, TaskState
from .state import reduce_run, reduce_task


@dataclass(frozen=True, slots=True)
class CommandRecord:
    """Durable idempotency reservation for one logical mutating command."""

    scope: str
    idempotency_key: str
    operation: str
    stream_id: str
    result_id: str
    event_id: str


@dataclass(frozen=True, slots=True)
class CommitResult:
    applied: bool
    revision: int
    command: CommandRecord | None = None


@runtime_checkable
class EventRepository(Protocol):
    """Atomic append-only canonical event storage with optimistic revision checks."""

    async def read_events(self, stream_id: str) -> tuple[PlatformEvent, ...]: ...

    async def revision(self, stream_id: str) -> int: ...

    async def commit(
        self,
        *,
        stream_id: str,
        expected_revision: int,
        events: tuple[PlatformEvent, ...],
        command: CommandRecord | None = None,
    ) -> CommitResult: ...

    async def find_command(self, scope: str, idempotency_key: str) -> CommandRecord | None: ...

    async def list_stream_ids(self) -> tuple[str, ...]: ...


@runtime_checkable
class TaskRepository(Protocol):
    async def get_task(self, task_id: str) -> TaskState: ...


@runtime_checkable
class RunRepository(Protocol):
    async def get_run(self, task_id: str, run_id: str) -> RunState: ...


class EventSourcedTaskRepository(TaskRepository):
    """Task repository reconstructed from the authoritative event stream."""

    def __init__(self, events: EventRepository) -> None:
        self._events = events

    async def get_task(self, task_id: str) -> TaskState:
        return reduce_task(await self._events.read_events(task_id), task_id)


class EventSourcedRunRepository(RunRepository):
    """Run repository reconstructed from the authoritative task event stream."""

    def __init__(self, events: EventRepository) -> None:
        self._events = events

    async def get_run(self, task_id: str, run_id: str) -> RunState:
        return reduce_run(await self._events.read_events(task_id), run_id)


class InMemoryKernelRepository(EventRepository):
    """Atomic in-memory baseline used for deterministic unit and fake-adapter flows."""

    def __init__(self) -> None:
        self._streams: dict[str, list[PlatformEvent]] = {}
        self._commands: dict[tuple[str, str], CommandRecord] = {}
        self._lock = asyncio.Lock()

    async def read_events(self, stream_id: str) -> tuple[PlatformEvent, ...]:
        async with self._lock:
            return tuple(self._streams.get(stream_id, []))

    async def revision(self, stream_id: str) -> int:
        async with self._lock:
            return len(self._streams.get(stream_id, []))

    async def find_command(self, scope: str, idempotency_key: str) -> CommandRecord | None:
        async with self._lock:
            return self._commands.get((scope, idempotency_key))

    async def list_stream_ids(self) -> tuple[str, ...]:
        async with self._lock:
            return tuple(sorted(self._streams))

    async def commit(
        self,
        *,
        stream_id: str,
        expected_revision: int,
        events: tuple[PlatformEvent, ...],
        command: CommandRecord | None = None,
    ) -> CommitResult:
        if expected_revision < 0:
            raise ValueError("expected_revision must be >= 0")
        if not events:
            raise ValueError("kernel commits must contain at least one event")

        async with self._lock:
            if command is not None:
                existing = self._commands.get((command.scope, command.idempotency_key))
                if existing is not None:
                    return CommitResult(
                        applied=False,
                        revision=len(self._streams.get(existing.stream_id, [])),
                        command=existing,
                    )

            stream = self._streams.setdefault(stream_id, [])
            if len(stream) != expected_revision:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"stale stream revision for {stream_id}: "
                    f"expected {expected_revision}, actual {len(stream)}",
                )

            existing_event_ids = {
                event.event_id for item in self._streams.values() for event in item
            }
            for event in events:
                if event.context.correlation_id != stream_id:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "event correlation_id must equal canonical stream id",
                    )
                if event.event_id in existing_event_ids:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        f"duplicate event id: {event.event_id}",
                    )
                existing_event_ids.add(event.event_id)

            stream.extend(events)
            if command is not None:
                self._commands[(command.scope, command.idempotency_key)] = command
            return CommitResult(
                applied=True,
                revision=len(stream),
                command=command,
            )
