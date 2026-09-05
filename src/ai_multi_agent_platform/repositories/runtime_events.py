"""Durable repository-event ingress for the canonical AutomationRuntime event stream."""

from __future__ import annotations

from ai_multi_agent_platform.connectors import ConnectorEvent
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import PlatformEvent
from ai_multi_agent_platform.kernel import CommandRecord, EventRepository

from .events import repository_platform_event
from .service import RepositoryBinding, RepositoryRegistry

_EVENT_SCOPE = "repository-event-ingress"
_MAX_COMMIT_ATTEMPTS = 4


class RepositoryEventRuntimeIngress:
    """Append verified repository events to the durable event stream consumed by #18.

    Connector transports first normalize and verify provider material as ``ConnectorEvent``.
    This ingress then resolves the canonical Repository binding, applies the shared repository
    event contract and commits the resulting ``PlatformEvent`` into the same ``EventRepository``
    polled by ``AutomationRuntime``. No repository event directly executes privileged work.
    """

    def __init__(
        self,
        registry: RepositoryRegistry,
        events: EventRepository,
        *,
        require_verified: bool = True,
    ) -> None:
        self._registry = registry
        self._events = events
        self._require_verified = require_verified

    async def publish(
        self,
        event: ConnectorEvent,
        *,
        correlation_id: str,
    ) -> PlatformEvent:
        binding = self._resolve_binding(event)
        canonical = repository_platform_event(
            event,
            binding,
            correlation_id=correlation_id,
            require_verified=self._require_verified,
        )
        return await self._append_once(canonical)

    def _resolve_binding(self, event: ConnectorEvent) -> RepositoryBinding:
        repository_ids: list[str] = []
        if event.resource_id is not None:
            repository_ids.append(event.resource_id)
        payload_repository_id = event.payload.get("repository_id")
        if isinstance(payload_repository_id, str) and payload_repository_id.strip():
            repository_ids.append(payload_repository_id)

        for repository_id in dict.fromkeys(repository_ids):
            try:
                binding = self._registry.resolve(repository_id)
            except ContractError as exc:
                if exc.code is ErrorCode.NOT_FOUND:
                    continue
                raise
            if binding.connection.id != event.connection_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "repository event references a repository from another connection",
                    provider_id=binding.provider.provider_id,
                )
            return binding

        candidates = self._registry.list(connection_id=event.connection_id)
        if not candidates:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "repository event connection has no registered repository binding",
            )
        if len(candidates) != 1:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository event is ambiguous across multiple repositories on one connection",
                details={"connection_id": event.connection_id, "candidate_count": len(candidates)},
            )
        return candidates[0]

    async def _append_once(self, event: PlatformEvent) -> PlatformEvent:
        stream_id = event.project_id
        if stream_id is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository runtime event must have project scope",
            )
        command = CommandRecord(
            scope=_EVENT_SCOPE,
            idempotency_key=event.id,
            operation="repository.event.ingress",
            stream_id=stream_id,
            result_id=event.id,
            event_id=event.id,
        )

        for _ in range(_MAX_COMMIT_ATTEMPTS):
            expected_revision = await self._events.revision(stream_id)
            try:
                result = await self._events.commit(
                    stream_id=stream_id,
                    expected_revision=expected_revision,
                    events=(event,),
                    command=command,
                )
            except ContractError as exc:
                if exc.code is ErrorCode.CONFLICT:
                    continue
                raise

            if result.applied:
                return event

            existing_command = result.command
            existing_stream_id = (
                existing_command.stream_id if existing_command is not None else stream_id
            )
            for existing in await self._events.read_events(existing_stream_id):
                if existing.id == event.id:
                    return existing
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository event idempotency record exists without its canonical event",
                details={"event_id": event.id},
            )

        raise ContractError(
            ErrorCode.CONFLICT,
            "repository event stream remained concurrently modified during ingress",
            retryable=True,
            details={"event_id": event.id, "stream_id": stream_id},
        )
