"""Canonical organization/team/membership audit projection for issue #87."""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import EventProvider
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain.models import Event, OwnerRef, Provenance
from ai_multi_agent_platform.organizations import MembershipStatus, OrganizationService

from .extensions import ResourceService
from .models import PageQuery, RequestContext
from .organization_runtime_composition import ORGANIZATION_RUNTIME_COMMANDS
from .organization_runtime_composition import ControlPlane as _OrganizationControlPlane

ORGANIZATION_AUDIT_COLLECTION = "organization-audit-events"
ORGANIZATION_AUDIT_SOURCE = "control-plane.organization-audit"
AUDITED_ORGANIZATION_COMMANDS = frozenset(
    {
        "organization.create",
        "organization.update",
        "organization.archive",
        "team.create",
        "team.update",
        "team.configure",
        "membership.add",
        "membership.assign",
        "membership.suspend",
        "membership.remove",
        "membership.leave",
        "invitation.create",
        "invitation.accept",
        "invitation.revoke",
        "external-group-mapping.create",
        "external-group-mapping.deactivate",
    }
)


class OrganizationAuditLog:
    """Projects successful northbound organization mutations into canonical Events."""

    def __init__(self, events: EventProvider) -> None:
        self._events = events

    async def record_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        result: dict[str, JsonValue],
    ) -> Event | None:
        if command not in AUDITED_ORGANIZATION_COMMANDS:
            return None
        organization_id = _organization_id(command, result)
        if organization_id is None:
            return None
        event_id = _audit_event_id(context, command, resource_ref)
        event = Event(
            id=event_id,
            event_type=command,
            subject_type="event",
            subject_id=event_id,
            correlation_id=organization_id,
            causation_id=context.idempotency_key,
            owner_ref=OwnerRef(type="organization", id=organization_id),
            payload=_audit_payload(command, resource_ref, result),
            provenance=Provenance(
                source=ORGANIZATION_AUDIT_SOURCE,
                actor_ref=context.actor.principal_ref,
                details={
                    "request_id": context.request_id,
                    "correlation_id": context.correlation_id,
                },
            ),
        )
        await self._events.publish(event)
        return event

    async def read_organization_history(self, organization_id: str) -> tuple[Event, ...]:
        events = await self._events.read(organization_id)
        return tuple(
            event
            for event in events
            if event.provenance is not None
            and event.provenance.source == ORGANIZATION_AUDIT_SOURCE
            and event.event_type in AUDITED_ORGANIZATION_COMMANDS
        )


class _OrganizationAuditResources(ResourceService):
    def __init__(self, service: OrganizationService, audit: OrganizationAuditLog) -> None:
        self._service = service
        self._audit = audit

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        visible = await _visible_organization_ids(self._service, context.actor.principal_ref)
        requested_organization = (
            None if query.filters is None else query.filters.get("organization_id")
        )
        organization_ids: tuple[str, ...]
        if requested_organization is not None:
            if requested_organization not in visible:
                return ()
            organization_ids = (requested_organization,)
        else:
            organization_ids = tuple(sorted(visible))

        resources: list[dict[str, JsonValue]] = []
        for organization_id in organization_ids:
            for event in await self._audit.read_organization_history(organization_id):
                resources.append(_audit_resource(event))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        visible = await _visible_organization_ids(self._service, context.actor.principal_ref)
        for organization_id in visible:
            for event in await self._audit.read_organization_history(organization_id):
                if event.id == resource_id:
                    return _audit_resource(event)
        raise ContractError(
            ErrorCode.NOT_FOUND, f"organization audit event not found: {resource_id}"
        )


class ControlPlane(_OrganizationControlPlane):
    """Organization Control Plane plus optional canonical mutation audit history."""

    def __init__(
        self,
        *args: Any,
        organization_audit_events: EventProvider | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._organization_audit = (
            None
            if organization_audit_events is None
            else OrganizationAuditLog(organization_audit_events)
        )
        if self._organization_audit is not None and self.organization_service is not None:
            super().register_resource_service(
                ORGANIZATION_AUDIT_COLLECTION,
                _OrganizationAuditResources(self.organization_service, self._organization_audit),
            )

    @property
    def organization_audit(self) -> OrganizationAuditLog | None:
        return self._organization_audit

    def register_resource_service(self, collection: str, service: ResourceService) -> None:
        if collection == ORGANIZATION_AUDIT_COLLECTION:
            raise ValueError(
                "extension collection conflicts with canonical organization audit "
                f"route: {collection}"
            )
        super().register_resource_service(collection, service)

    async def execute_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        result = await super().execute_command(context, command, resource_ref, payload)
        if self._organization_audit is not None and command in ORGANIZATION_RUNTIME_COMMANDS:
            await self._organization_audit.record_command(context, command, resource_ref, result)
        return result


def _audit_event_id(context: RequestContext, command: str, resource_ref: str) -> str:
    logical_key = context.idempotency_key or context.request_id
    value = uuid5(
        NAMESPACE_URL,
        f"ai-multi-agent-platform:organization-audit:{logical_key}:{command}:{resource_ref}",
    )
    return f"event_{value}"


def _organization_id(command: str, result: dict[str, JsonValue]) -> str | None:
    if command.startswith("organization."):
        value = result.get("id")
    else:
        value = result.get("organization_id")
    return value if isinstance(value, str) and value else None


def _audit_payload(
    command: str,
    resource_ref: str,
    result: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "command": command,
        "resource_ref": resource_ref,
    }
    for source, target in (
        ("type", "resource_type"),
        ("id", "resource_id"),
        ("organization_id", "organization_id"),
        ("team_id", "team_id"),
        ("actor_id", "affected_actor_id"),
        ("status", "status"),
    ):
        value = result.get(source)
        if isinstance(value, str):
            payload[target] = value
    for field in ("role_refs", "policy_refs"):
        value = result.get(field)
        if isinstance(value, list):
            projected: list[JsonValue] = []
            for item in value:
                if isinstance(item, str):
                    projected.append(item)
            payload[field] = projected
    return payload


def _audit_resource(event: Event) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {
        "id": event.id,
        "type": "organization_audit_event",
        "organization_id": event.correlation_id,
        "event_type": event.event_type,
        "occurred_at": event.occurred_at.isoformat(),
    }
    for field in (
        "command",
        "resource_ref",
        "resource_type",
        "resource_id",
        "team_id",
        "affected_actor_id",
        "status",
    ):
        value = event.payload.get(field)
        if isinstance(value, str):
            result[field] = value
    for field in ("role_refs", "policy_refs"):
        value = event.payload.get(field)
        if isinstance(value, tuple):
            projected: list[JsonValue] = []
            for item in value:
                if isinstance(item, str):
                    projected.append(item)
            result[field] = projected
    if event.provenance is not None:
        if event.provenance.actor_ref is not None:
            result["actor_ref"] = event.provenance.actor_ref
        request_id = event.provenance.details.get("request_id")
        if isinstance(request_id, str):
            result["request_id"] = request_id
    return result


async def _visible_organization_ids(
    service: OrganizationService,
    principal_ref: str,
) -> frozenset[str]:
    visible: set[str] = set()
    for organization in await service.repository.list_organizations():
        if (
            principal_ref == organization.owner_actor_id
            or principal_ref in organization.administrator_actor_ids
        ):
            visible.add(organization.id)
    for membership in await service.repository.list_memberships(actor_id=principal_ref):
        if membership.status is MembershipStatus.ACTIVE:
            visible.add(membership.organization_id)
    return frozenset(visible)
