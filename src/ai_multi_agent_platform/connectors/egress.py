"""Egress-enforced application service for all outbound connector transport."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace

from ai_multi_agent_platform.contracts import (
    DataClassification,
    EgressRequest,
    EgressTarget,
    EgressTargetKind,
    EgressTargetPosture,
    JsonValue,
    OperationContext,
    digest_egress_payload,
    strongest_classification,
)
from ai_multi_agent_platform.security import ActorIdentity
from ai_multi_agent_platform.security.egress import EgressGate

from .models import (
    ConnectorActionResult,
    ConnectorEvent,
    ConnectorSyncResult,
    ExternalNativeReference,
    ExternalResourceReference,
    SyncMode,
)
from .registry import ConnectorRegistry
from .repository import ConnectorRepository
from .service import ConnectorService

type ConnectorClassificationResolver = Callable[[OperationContext], DataClassification]


class EgressConnectorService(ConnectorService):
    """Canonical application-facing ConnectorService with mandatory outbound policy checks.

    The legacy base service remains implementation-compatible for internal/local tests. Production
    composition should use this class so direct control-plane calls and capability-bridge calls
    cannot bypass the same egress gate. Provider-returned data inherits the strongest originating
    classification so a connector cannot silently downgrade data derived from protected input.
    """

    def __init__(
        self,
        repository: ConnectorRepository,
        registry: ConnectorRegistry,
        *,
        authorization_gate: object | None = None,
        egress_gate: EgressGate | None = None,
        classification_resolver: ConnectorClassificationResolver | None = None,
        target_postures: Mapping[str, EgressTargetPosture] | None = None,
        allowed_classifications: Mapping[str, tuple[DataClassification, ...]] | None = None,
        sensitive_external_targets: frozenset[str] = frozenset(),
    ) -> None:
        # Avoid widening ConnectorService's public authorization type through a circular import.
        super().__init__(repository, registry, authorization_gate=authorization_gate)  # type: ignore[arg-type]
        self.egress_gate = egress_gate or EgressGate()
        self._classification_resolver = classification_resolver
        self._target_postures = dict(target_postures or {})
        self._allowed_classifications = dict(allowed_classifications or {})
        self._sensitive_external_targets = frozenset(sensitive_external_targets)

    async def list_resources(
        self,
        connection_id: str,
        resource_type: str,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        query: dict[str, JsonValue] | None = None,
        data_classification: DataClassification | None = None,
    ) -> tuple[ExternalResourceReference, ...]:
        classification = self._classification(context, data_classification)
        await self._enforce_connector_egress(
            connection_id,
            actor=actor,
            context=context,
            classification=classification,
            resource_type="connector_resource_query",
            payload={"resource_type": resource_type, "query": query or {}},
        )
        resources = await super().list_resources(
            connection_id,
            resource_type,
            actor=actor,
            context=context,
            query=query,
        )
        return tuple(_classified_resource(resource, classification) for resource in resources)

    async def read_resource(
        self,
        connection_id: str,
        resource: ExternalResourceReference,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        data_classification: DataClassification | None = None,
    ) -> ExternalResourceReference:
        classification = _inherited_classification(
            self._classification(context, data_classification),
            resource.classification,
        )
        await self._enforce_connector_egress(
            connection_id,
            actor=actor,
            context=context,
            classification=classification,
            resource_type="connector_resource_read",
            payload={
                "external_resource_id": resource.id,
                "resource_type": resource.resource_type,
            },
        )
        refreshed = await super().read_resource(
            connection_id,
            resource,
            actor=actor,
            context=context,
        )
        return _classified_resource(refreshed, classification)

    async def invoke_action(
        self,
        connection_id: str,
        action: str,
        arguments: dict[str, JsonValue],
        *,
        invocation_id: str,
        actor: ActorIdentity,
        context: OperationContext,
        approval_id: str | None = None,
        data_classification: DataClassification | None = None,
    ) -> ConnectorActionResult:
        classification = self._classification(context, data_classification)
        await self._enforce_connector_egress(
            connection_id,
            actor=actor,
            context=context,
            classification=classification,
            resource_type="connector_action",
            payload={
                "connection_id": connection_id,
                "action": action,
                "arguments": arguments,
                "invocation_id": invocation_id,
            },
            capability_id=action,
            approval_id=approval_id,
        )
        result = await super().invoke_action(
            connection_id,
            action,
            arguments,
            invocation_id=invocation_id,
            actor=actor,
            context=context,
            approval_id=approval_id,
        )
        effective = _inherited_classification(classification, result.classification)
        return replace(
            result,
            classification=effective,
            resource_refs=tuple(
                _classified_resource(resource, effective) for resource in result.resource_refs
            ),
        )

    async def subscribe_events(
        self,
        connection_id: str,
        event_types: tuple[str, ...],
        *,
        configuration: dict[str, JsonValue],
        actor: ActorIdentity,
        context: OperationContext,
        approval_id: str | None = None,
        data_classification: DataClassification | None = None,
    ) -> ExternalNativeReference:
        await self._enforce_connector_egress(
            connection_id,
            actor=actor,
            context=context,
            classification=self._classification(context, data_classification),
            resource_type="connector_event_subscription",
            payload={"event_types": list(event_types), "configuration": configuration},
            approval_id=approval_id,
        )
        return await super().subscribe_events(
            connection_id,
            event_types,
            configuration=configuration,
            actor=actor,
            context=context,
            approval_id=approval_id,
        )

    async def unsubscribe_events(
        self,
        connection_id: str,
        subscription: ExternalNativeReference,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        approval_id: str | None = None,
        data_classification: DataClassification | None = None,
    ) -> None:
        await self._enforce_connector_egress(
            connection_id,
            actor=actor,
            context=context,
            classification=self._classification(context, data_classification),
            resource_type="connector_event_unsubscription",
            payload={
                "subscription_namespace": subscription.namespace,
                "subscription_native_id": subscription.native_id,
            },
            approval_id=approval_id,
        )
        await super().unsubscribe_events(
            connection_id,
            subscription,
            actor=actor,
            context=context,
            approval_id=approval_id,
        )

    async def synchronize(
        self,
        connection_id: str,
        stream: str,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        mode: SyncMode = SyncMode.INCREMENTAL,
        data_classification: DataClassification | None = None,
    ) -> ConnectorSyncResult:
        classification = self._classification(context, data_classification)
        await self._enforce_connector_egress(
            connection_id,
            actor=actor,
            context=context,
            classification=classification,
            resource_type="connector_sync",
            payload={"stream": stream, "mode": mode.value},
        )
        result = await super().synchronize(
            connection_id,
            stream,
            actor=actor,
            context=context,
            mode=mode,
        )
        return replace(
            result,
            resources=tuple(
                _classified_resource(resource, classification) for resource in result.resources
            ),
            events=tuple(_classified_event(event, classification) for event in result.events),
        )

    def _classification(
        self,
        context: OperationContext,
        override: DataClassification | None,
    ) -> DataClassification:
        if override is not None:
            return override
        if self._classification_resolver is not None:
            return self._classification_resolver(context)
        return DataClassification.INTERNAL

    async def _enforce_connector_egress(
        self,
        connection_id: str,
        *,
        actor: ActorIdentity,
        context: OperationContext,
        classification: DataClassification,
        resource_type: str,
        payload: JsonValue,
        capability_id: str | None = None,
        approval_id: str | None = None,
    ) -> None:
        posture = self._target_postures.get(connection_id, EgressTargetPosture.EXTERNAL)
        await self.egress_gate.enforce(
            EgressRequest(
                request_id=(
                    f"connector:{connection_id}:{context.correlation_id}:"
                    f"{digest_egress_payload(payload)[:16]}"
                ),
                target=EgressTarget(
                    kind=EgressTargetKind.CONNECTOR,
                    target_id=connection_id,
                    posture=posture,
                    allowed_classifications=self._allowed_classifications.get(connection_id, ()),
                    policy_metadata={
                        "allow_sensitive_external": (
                            connection_id in self._sensitive_external_targets
                        )
                    },
                ),
                context=context,
                classification=classification,
                resource_type=resource_type,
                payload_digest=digest_egress_payload(payload),
                capability_id=capability_id,
                policy_descriptors={"connection_id": connection_id},
            ),
            actor=actor,
            approval_id=approval_id,
        )


def _classified_resource(
    resource: ExternalResourceReference,
    source: DataClassification,
) -> ExternalResourceReference:
    return replace(
        resource,
        classification=_inherited_classification(source, resource.classification),
    )


def _classified_event(event: ConnectorEvent, source: DataClassification) -> ConnectorEvent:
    return replace(
        event,
        classification=_inherited_classification(source, event.classification),
    )


def _inherited_classification(
    source: DataClassification,
    reported: DataClassification | str | None,
) -> DataClassification:
    normalized: DataClassification | None
    if reported is None:
        normalized = None
    elif isinstance(reported, DataClassification):
        normalized = reported
    else:
        normalized = DataClassification(reported)
    effective = strongest_classification(source, normalized)
    if effective is None:
        raise AssertionError("source classification must produce an effective classification")
    return effective
