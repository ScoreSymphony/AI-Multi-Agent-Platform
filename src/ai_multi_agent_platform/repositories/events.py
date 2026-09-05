"""Normalize repository connector evidence into the canonical platform Event stream."""

from __future__ import annotations

import json
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.connectors import ConnectorEvent, ExternalResourceReference
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, EventProvider
from ai_multi_agent_platform.contracts.types import JsonValue, PlatformEvent
from ai_multi_agent_platform.domain import ExternalRef, Provenance

from .service import RepositoryBinding


def repository_platform_event_id(event: ConnectorEvent) -> str:
    """Derive a stable canonical Event ID from the connector-owned dedupe identity."""

    identity = json.dumps(
        [event.connection_id, event.connector_type_id, event.dedupe_key],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"event_{uuid5(NAMESPACE_URL, identity)}"


class RepositoryEventBridge:
    """Publish verified repository events once through the platform-owned EventProvider.

    Deduplication is durable when the configured EventProvider satisfies the canonical
    EventProvider contract: the same ConnectorEvent dedupe key deterministically maps to
    the same canonical Event ID, and EventProvider.publish is idempotent by that ID.
    """

    def __init__(self, events: EventProvider, *, require_verified: bool = True) -> None:
        self._events = events
        self._require_verified = require_verified

    async def publish(
        self,
        event: ConnectorEvent,
        binding: RepositoryBinding,
        *,
        correlation_id: str,
    ) -> PlatformEvent:
        connection = binding.connection.connection
        repository = binding.reference
        if event.connection_id != connection.id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository connector event belongs to another connection",
                provider_id=binding.provider.provider_id,
            )
        if event.connector_type_id != connection.connector_type_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository connector event type does not match the bound connection",
                provider_id=binding.provider.provider_id,
            )
        if self._require_verified and not event.verified:
            raise ContractError(
                ErrorCode.UNAUTHORIZED,
                "unverified repository connector event cannot enter the canonical event stream",
                provider_id=binding.provider.provider_id,
            )
        project_id = event.project_id or connection.project_id
        if project_id is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository connector event needs project scope for a canonical event subject",
                provider_id=binding.provider.provider_id,
            )
        if connection.project_id is not None and project_id != connection.project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "repository connector event project scope does not match the connection",
                provider_id=binding.provider.provider_id,
            )
        if not correlation_id.strip():
            raise ValueError("repository event correlation_id must not be blank")

        native_event_value = (
            f"{event.native_reference.namespace}:{event.native_reference.native_id}"
        )
        external_refs = (
            ExternalRef(
                system=event.connector_type_id,
                kind="repository_event",
                value=native_event_value,
            ),
        )
        canonical = PlatformEvent(
            id=repository_platform_event_id(event),
            event_type=f"repository.external.{event.event_type}",
            subject_type="project",
            subject_id=project_id,
            correlation_id=correlation_id,
            project_id=project_id,
            occurred_at=event.received_at,
            payload={
                "repository_id": repository.id,
                "connection_id": connection.id,
                "connector_type_id": event.connector_type_id,
                "connector_event_id": event.id,
                "dedupe_key": event.dedupe_key,
                "resource_id": event.resource_id,
                "verified": event.verified,
                "schema_version": event.schema_version,
                "payload": dict(event.payload),
                "provenance": dict(event.provenance),
            },
            provenance=Provenance(
                source=f"connector:{event.connector_type_id}",
                details={
                    "connection_id": connection.id,
                    "repository_id": repository.id,
                    "connector_event_id": event.id,
                    "dedupe_key": event.dedupe_key,
                    "verified": event.verified,
                },
            ),
            external_refs=external_refs,
        )
        await self._events.publish(canonical)
        return canonical


def repository_resource_payload(reference: ExternalResourceReference) -> dict[str, JsonValue]:
    """Return safe external repository evidence for event/API payloads without credentials."""

    return reference.to_dict()
