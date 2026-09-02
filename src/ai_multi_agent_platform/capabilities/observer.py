"""Persistent observability adapters for canonical capability invocation records."""

from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts.types import JsonValue, PlatformEvent
from ai_multi_agent_platform.kernel.repository import EventRepository

from .types import InvocationRecord


class EventRepositoryInvocationObserver:
    """Append invocation lifecycle records to the canonical event repository.

    The observer intentionally persists metadata and references, not raw tool input/output bodies.
    This keeps the default audit stream useful without turning it into a secret-bearing payload log.
    Backend-private metadata remains namespaced.
    """

    def __init__(self, repository: EventRepository) -> None:
        self._repository = repository
        self._lock = asyncio.Lock()

    @staticmethod
    def stream_id(invocation_id: str) -> str:
        return f"capability-invocation:{invocation_id}"

    async def record(self, record: InvocationRecord) -> None:
        stream_id = self.stream_id(record.invocation_id)
        event = PlatformEvent(
            event_type=f"capability.invocation.{record.status.value}",
            subject_type="run",
            subject_id=record.trace.run_id,
            correlation_id=stream_id,
            project_id=record.trace.project_id,
            causation_id=record.trace.causation_id,
            trace_id=record.trace.correlation_id,
            occurred_at=record.recorded_at,
            payload=self._payload(record),
        )
        async with self._lock:
            revision = await self._repository.revision(stream_id)
            await self._repository.commit(
                stream_id=stream_id,
                expected_revision=revision,
                events=(event,),
            )

    @staticmethod
    def _payload(record: InvocationRecord) -> dict[str, object]:
        metadata: list[dict[str, JsonValue]] = [
            {
                "namespace": item.namespace,
                "values": dict(item.values),
            }
            for item in record.adapter_metadata
        ]
        return {
            "invocation_id": record.invocation_id,
            "capability_id": record.capability_id,
            "capability_version": record.capability_version,
            "provider_id": record.provider_id,
            "provider_tool_ref": record.provider_tool_ref,
            "status": record.status.value,
            "task_id": record.trace.task_id,
            "run_id": record.trace.run_id,
            "agent_id": record.trace.agent_id,
            "project_id": record.trace.project_id,
            "request_correlation_id": record.trace.correlation_id,
            "request_causation_id": record.trace.causation_id,
            "canonical_tool_invocation_id": record.canonical_tool_invocation_id,
            "node_id": record.node_id,
            "worker_id": record.worker_id,
            "approval_decision": record.approval_decision,
            "error_code": record.error_code,
            "adapter_metadata": metadata,
        }
