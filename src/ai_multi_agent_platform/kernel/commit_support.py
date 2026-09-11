"""Canonical command, event and commit support for the platform kernel.

This module keeps idempotency lookup, canonical event construction, event-store commits,
operation contexts and event mirroring behind one internal boundary.  Domain command
services remain responsible for deciding *which* transitions happen; this component only
applies the shared canonical commit mechanics.
"""

from __future__ import annotations

from typing import Literal, Protocol

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    EventProvider,
    OperationContext,
    OperationControl,
    PlatformEvent,
    RetryMode,
)
from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue
from ai_multi_agent_platform.domain import Event as DomainEvent
from ai_multi_agent_platform.domain import OwnerRef, Provenance

from .models import TaskState
from .repository import CommandRecord, EventRepository

OwnerType = Literal["user", "organization", "team", "service"]
EventSpec = tuple[
    str,
    str,
    str,
    dict[str, JsonValue],
    tuple[AdapterMetadata, ...],
]


class CommitKernelHost(Protocol):
    """Internal storage/sink dependencies required by canonical commit support."""

    _repository: EventRepository
    _event_sink: EventProvider | None


class KernelCommitSupport:
    """Apply shared idempotency, event-construction and commit mechanics."""

    def __init__(self, host: CommitKernelHost) -> None:
        self._host = host

    async def task_command(
        self,
        task_id: str,
        key: str,
        operation: str,
    ) -> CommandRecord | None:
        self.require_key(key)
        return await self.existing_command(task_id, key, operation)

    async def existing_command(
        self,
        scope: str,
        key: str,
        operation: str,
    ) -> CommandRecord | None:
        record = await self._host._repository.find_command(scope, key)
        if record is None:
            return None
        return self.require_same_command(record, operation, key)

    @staticmethod
    def require_same_command(
        record: CommandRecord | None,
        operation: str,
        key: str,
    ) -> CommandRecord:
        if record is None:
            raise ContractError(ErrorCode.CONFLICT, f"idempotency race lost for {key}")
        if record.operation != operation:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"idempotency key {key!r} already belongs to {record.operation}",
            )
        return record

    async def commit_task_command(
        self,
        *,
        task: TaskState,
        key: str,
        operation: str,
        event_specs: tuple[EventSpec, ...],
        result_id: str,
        actor_ref: str | None,
        source: str,
    ) -> CommandRecord:
        self.require_key(key)
        existing = await self.existing_command(task.task_id, key, operation)
        if existing is not None:
            return existing
        events = self.build_events(
            task=task,
            causation_id=key,
            actor_ref=actor_ref,
            source=source,
            event_specs=event_specs,
        )
        command = self.command(
            scope=task.task_id,
            key=key,
            operation=operation,
            stream_id=task.task_id,
            result_id=result_id,
            event=events[0],
        )
        result = await self._host._repository.commit(
            stream_id=task.task_id,
            expected_revision=task.revision,
            events=events,
            command=command,
        )
        if not result.applied:
            return self.require_same_command(result.command, operation, key)
        await self.mirror(events)
        return command

    async def append_system_events(
        self,
        *,
        task: TaskState,
        causation_id: str,
        actor_ref: str | None,
        source: str,
        event_specs: tuple[EventSpec, ...],
    ) -> None:
        events = self.build_events(
            task=task,
            causation_id=causation_id,
            actor_ref=actor_ref,
            source=source,
            event_specs=event_specs,
        )
        await self._host._repository.commit(
            stream_id=task.task_id,
            expected_revision=task.revision,
            events=events,
        )
        await self.mirror(events)

    def build_events(
        self,
        *,
        task: TaskState,
        causation_id: str,
        actor_ref: str | None,
        source: str,
        event_specs: tuple[EventSpec, ...],
    ) -> tuple[PlatformEvent, ...]:
        return tuple(
            self.event(
                stream_id=task.task_id,
                event_type=event_type,
                subject_type=subject_type,
                subject_id=subject_id,
                causation_id=causation_id,
                owner_type=task.task.owner_ref.type,
                owner_id=task.task.owner_ref.id,
                project_id=task.task.project_id,
                actor_ref=actor_ref or f"{task.task.owner_ref.type}:{task.task.owner_ref.id}",
                source=source,
                revision=task.revision + offset,
                payload=payload,
                adapter_metadata=adapter_metadata,
            )
            for offset, (
                event_type,
                subject_type,
                subject_id,
                payload,
                adapter_metadata,
            ) in enumerate(event_specs, start=1)
        )

    @staticmethod
    def event(
        *,
        stream_id: str,
        event_type: str,
        subject_type: str,
        subject_id: str,
        causation_id: str,
        owner_type: OwnerType,
        owner_id: str,
        project_id: str | None,
        actor_ref: str,
        source: str,
        revision: int,
        payload: dict[str, JsonValue],
        adapter_metadata: tuple[AdapterMetadata, ...] = (),
    ) -> PlatformEvent:
        enriched = dict(payload)
        enriched.update(
            {
                "actor_ref": actor_ref,
                "source": source,
                "canonical_payload_version": "1.0",
                "stream_revision": revision,
            }
        )
        if adapter_metadata:
            namespaces = [item.namespace for item in adapter_metadata]
            if len(namespaces) != len(set(namespaces)):
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "adapter metadata namespaces must be unique",
                )
            enriched["adapter_metadata"] = {
                item.namespace: dict(item.values) for item in adapter_metadata
            }

        return DomainEvent(
            event_type=event_type,
            subject_type=subject_type,
            subject_id=subject_id,
            correlation_id=stream_id,
            owner_ref=OwnerRef(type=owner_type, id=owner_id),
            project_id=project_id,
            causation_id=causation_id,
            payload=enriched,
            provenance=Provenance(source=source, actor_ref=actor_ref),
        )

    @staticmethod
    def command(
        *,
        scope: str,
        key: str,
        operation: str,
        stream_id: str,
        result_id: str,
        event: PlatformEvent,
    ) -> CommandRecord:
        return CommandRecord(
            scope=scope,
            idempotency_key=key,
            operation=operation,
            stream_id=stream_id,
            result_id=result_id,
            event_id=event.id,
        )

    @staticmethod
    def context(task: TaskState, causation_id: str) -> OperationContext:
        return OperationContext(
            correlation_id=task.task_id,
            causation_id=causation_id,
            owner_type=task.task.owner_ref.type,
            owner_id=task.task.owner_ref.id,
            project_id=task.task.project_id,
            control=OperationControl(
                idempotency_key=causation_id,
                retry_mode=RetryMode.IDEMPOTENT,
            ),
        )

    async def mirror(self, events: tuple[PlatformEvent, ...]) -> None:
        if self._host._event_sink is None:
            return
        for event in events:
            await self._host._event_sink.publish(event)

    @staticmethod
    def require_key(key: str) -> None:
        if not key.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "idempotency key must not be blank")


__all__ = ["KernelCommitSupport"]
