"""Durable reference runtime for canonical Automation scheduling and event ingestion."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, PlatformEvent
from ai_multi_agent_platform.kernel.repository import EventRepository

from .models import require_aware, utc_now
from .runtime_service import AutomationService
from .service import ReferenceScheduler

_RETRYABLE_EVENT_ERROR_CODES = frozenset(
    {
        ErrorCode.UNAVAILABLE,
        ErrorCode.TIMEOUT,
        ErrorCode.RATE_LIMITED,
        ErrorCode.RESOURCE_EXHAUSTED,
        ErrorCode.TRANSIENT_FAILURE,
        ErrorCode.BACKEND_ERROR,
    }
)


@dataclass(frozen=True, slots=True)
class AutomationCommandRecord:
    """Durable replay record for one northbound Automation command."""

    principal_ref: str
    idempotency_key: str
    command: str
    resource_ref: str
    payload_digest: str
    result: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class AutomationRuntimeTick:
    """Observable result of one deterministic runtime evaluation pass."""

    processed_event_ids: tuple[str, ...] = ()
    failed_event_ids: tuple[str, ...] = ()
    terminal_event_ids: tuple[str, ...] = ()
    event_delivery_ids: tuple[str, ...] = ()
    schedule_delivery_ids: tuple[str, ...] = ()


class AutomationRuntimeState(ABC):
    """Replaceable persistence seam for runtime cursors, command replay and audit data."""

    @abstractmethod
    async def get_command(
        self, principal_ref: str, idempotency_key: str
    ) -> AutomationCommandRecord | None: ...

    @abstractmethod
    async def save_command(self, record: AutomationCommandRecord) -> AutomationCommandRecord: ...

    @abstractmethod
    async def has_processed_event(self, event_id: str) -> bool: ...

    @abstractmethod
    async def mark_processed_event(self, event_id: str) -> None: ...

    @abstractmethod
    async def append_audit_event(self, event: dict[str, JsonValue]) -> None: ...

    @abstractmethod
    async def list_audit_events(self) -> tuple[dict[str, JsonValue], ...]: ...


class InMemoryAutomationRuntimeState(AutomationRuntimeState):
    """Deterministic ephemeral runtime state for tests and explicitly ephemeral embeddings."""

    def __init__(self) -> None:
        self._commands: dict[tuple[str, str], AutomationCommandRecord] = {}
        self._processed_events: set[str] = set()
        self._audit_events: list[dict[str, JsonValue]] = []

    async def get_command(
        self, principal_ref: str, idempotency_key: str
    ) -> AutomationCommandRecord | None:
        return self._commands.get((principal_ref, idempotency_key))

    async def save_command(self, record: AutomationCommandRecord) -> AutomationCommandRecord:
        key = (record.principal_ref, record.idempotency_key)
        existing = self._commands.get(key)
        if existing is not None:
            return existing
        self._commands[key] = record
        return record

    async def has_processed_event(self, event_id: str) -> bool:
        return event_id in self._processed_events

    async def mark_processed_event(self, event_id: str) -> None:
        self._processed_events.add(event_id)

    async def append_audit_event(self, event: dict[str, JsonValue]) -> None:
        self._audit_events.append(dict(event))

    async def list_audit_events(self) -> tuple[dict[str, JsonValue], ...]:
        return tuple(dict(event) for event in self._audit_events)


class SqliteAutomationRuntimeState(AutomationRuntimeState):
    """Restart-safe runtime state that may share the Automation repository SQLite file."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS automation_runtime_commands (
                        principal_ref TEXT NOT NULL,
                        idempotency_key TEXT NOT NULL,
                        command TEXT NOT NULL,
                        resource_ref TEXT NOT NULL,
                        payload_digest TEXT NOT NULL,
                        result TEXT NOT NULL,
                        PRIMARY KEY(principal_ref, idempotency_key)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS automation_runtime_processed_events (
                        event_id TEXT PRIMARY KEY
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS automation_runtime_audit (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        payload TEXT NOT NULL
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to initialize automation runtime state",
            ) from exc

    async def get_command(
        self, principal_ref: str, idempotency_key: str
    ) -> AutomationCommandRecord | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT command, resource_ref, payload_digest, result
                    FROM automation_runtime_commands
                    WHERE principal_ref = ? AND idempotency_key = ?
                    """,
                    (principal_ref, idempotency_key),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to read automation command replay state",
            ) from exc
        if row is None:
            return None
        result = cast(dict[str, JsonValue], json.loads(cast(str, row["result"])))
        return AutomationCommandRecord(
            principal_ref=principal_ref,
            idempotency_key=idempotency_key,
            command=cast(str, row["command"]),
            resource_ref=cast(str, row["resource_ref"]),
            payload_digest=cast(str, row["payload_digest"]),
            result=result,
        )

    async def save_command(self, record: AutomationCommandRecord) -> AutomationCommandRecord:
        encoded = json.dumps(record.result, sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO automation_runtime_commands(
                        principal_ref,
                        idempotency_key,
                        command,
                        resource_ref,
                        payload_digest,
                        result
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.principal_ref,
                        record.idempotency_key,
                        record.command,
                        record.resource_ref,
                        record.payload_digest,
                        encoded,
                    ),
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to persist automation command replay state",
            ) from exc
        existing = await self.get_command(record.principal_ref, record.idempotency_key)
        if existing is None:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "automation command replay state disappeared after persistence",
            )
        return existing

    async def has_processed_event(self, event_id: str) -> bool:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT 1 FROM automation_runtime_processed_events WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to read automation event cursor",
            ) from exc
        return row is not None

    async def mark_processed_event(self, event_id: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO automation_runtime_processed_events(event_id)
                    VALUES (?)
                    """,
                    (event_id,),
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to persist automation event cursor",
            ) from exc

    async def append_audit_event(self, event: dict[str, JsonValue]) -> None:
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO automation_runtime_audit(payload) VALUES (?)",
                    (encoded,),
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to persist automation audit event",
            ) from exc

    async def list_audit_events(self) -> tuple[dict[str, JsonValue], ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT payload FROM automation_runtime_audit ORDER BY sequence"
                ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to list automation audit events",
            ) from exc
        return tuple(
            cast(dict[str, JsonValue], json.loads(cast(str, row["payload"]))) for row in rows
        )


class AutomationRuntime:
    """Autonomous reference runner for schedules and canonical #6 events.

    The runtime deliberately polls the replaceable canonical ``EventRepository`` instead of
    depending on a broker. Processed event IDs are persisted so restart does not replay the
    canonical event stream indefinitely. Delivery-level deduplication remains the final guard.
    """

    def __init__(
        self,
        *,
        service: AutomationService,
        scheduler: ReferenceScheduler,
        events: EventRepository,
        state: AutomationRuntimeState,
        poll_interval_seconds: float = 1.0,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._service = service
        self._scheduler = scheduler
        self._events = events
        self._state = state
        self._poll_interval_seconds = poll_interval_seconds
        self._clock = clock
        self._stop_event = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None
        self._last_error: Exception | None = None

    @property
    def running(self) -> bool:
        return self._runner is not None and not self._runner.done()

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    async def run_once(self, *, now: datetime | None = None) -> AutomationRuntimeTick:
        current = require_aware(now or self._clock(), "now").astimezone(UTC)
        processed_event_ids: list[str] = []
        failed_event_ids: list[str] = []
        terminal_event_ids: list[str] = []
        event_delivery_ids: list[str] = []
        first_event_error: Exception | None = None

        pending_events = await self._pending_events()
        for event in pending_events:
            try:
                deliveries = await self._service.deliver_canonical_platform_event(event)
                event_delivery_ids.extend(delivery.id for delivery in deliveries)
                await self._state.mark_processed_event(event.id)
            except ContractError as exc:
                if _runtime_event_error_is_retryable(exc):
                    failed_event_ids.append(event.id)
                else:
                    try:
                        await self._state.append_audit_event(
                            _terminal_event_failure_audit(event, exc)
                        )
                        await self._state.mark_processed_event(event.id)
                    except Exception as persistence_exc:
                        failed_event_ids.append(event.id)
                        if first_event_error is None:
                            first_event_error = persistence_exc
                        continue
                    terminal_event_ids.append(event.id)
                if first_event_error is None:
                    first_event_error = exc
                continue
            except Exception as exc:
                # Unknown exceptions are conservatively retryable. The runtime must not discard a
                # canonical Event without a stable platform error category proving terminality.
                failed_event_ids.append(event.id)
                if first_event_error is None:
                    first_event_error = exc
                continue
            processed_event_ids.append(event.id)

        # Event ingestion failures must not starve the independent schedule path. Retryable Events
        # remain unacknowledged; permanent ContractErrors are auditable terminal outcomes. If
        # delivery creation succeeded but cursor persistence failed, TriggerDelivery deduplication
        # still protects the next attempt from duplicate work.
        schedule_deliveries = await self._scheduler.tick(now=current)
        self._last_error = first_event_error
        return AutomationRuntimeTick(
            processed_event_ids=tuple(processed_event_ids),
            failed_event_ids=tuple(failed_event_ids),
            terminal_event_ids=tuple(terminal_event_ids),
            event_delivery_ids=tuple(event_delivery_ids),
            schedule_delivery_ids=tuple(delivery.id for delivery in schedule_deliveries),
        )

    async def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._runner = asyncio.create_task(
            self._run_loop(),
            name="automation-reference-runtime",
        )

    async def stop(self) -> None:
        runner = self._runner
        if runner is None:
            return
        self._stop_event.set()
        await runner
        self._runner = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            run_failed = False
            try:
                await self.run_once()
            except Exception as exc:
                self._last_error = exc
                run_failed = True

            delay = self._poll_interval_seconds
            if not run_failed:
                try:
                    next_wakeup = await self._scheduler.next_wakeup()
                    if next_wakeup is not None:
                        remaining = (
                            next_wakeup.astimezone(UTC) - self._clock().astimezone(UTC)
                        ).total_seconds()
                        delay = min(delay, max(0.0, remaining))
                except Exception as exc:
                    self._last_error = exc
                    run_failed = True

            # A failed schedule/runtime pass must back off by at least the poll interval. Without
            # this guard an overdue schedule can keep next_wakeup at or before ``now`` and turn a
            # persistent backend failure into a zero-delay CPU loop.
            if run_failed:
                delay = self._poll_interval_seconds

            if delay <= 0:
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def _pending_events(self) -> tuple[PlatformEvent, ...]:
        pending: list[PlatformEvent] = []
        for stream_id in await self._events.list_stream_ids():
            for event in await self._events.read_events(stream_id):
                if not await self._state.has_processed_event(event.id):
                    pending.append(event)
        pending.sort(key=_event_sort_key)
        return tuple(pending)


def _runtime_event_error_is_retryable(error: ContractError) -> bool:
    return error.retryable or error.code in _RETRYABLE_EVENT_ERROR_CODES


def _terminal_event_failure_audit(
    event: PlatformEvent,
    error: ContractError,
) -> dict[str, JsonValue]:
    return {
        "type": "automation.runtime-event-terminal-failure",
        "event_id": event.id,
        "event_type": event.event_type,
        "subject_type": event.subject_type,
        "subject_id": event.subject_id,
        "project_id": event.project_id,
        "error_code": error.code.value,
        "error_message": error.message,
        "retryable": False,
    }


def _event_sort_key(event: PlatformEvent) -> tuple[int, float, str]:
    """Sort valid canonical timestamps first without letting one malformed Event block the scan."""

    try:
        occurred_at = require_aware(event.occurred_at, "event.occurred_at").astimezone(UTC)
        return (0, occurred_at.timestamp(), event.id)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return (1, 0.0, event.id)
