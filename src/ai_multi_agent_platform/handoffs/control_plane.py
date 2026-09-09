"""Read-only Control Plane projection for canonical Agent Handoff history."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .models import (
    AgentHandoff,
    HandoffConsumption,
    HandoffSourceRef,
    ParticipantRef,
    participant_key,
)
from .service import HandoffService

HANDOFF_COLLECTION = "agent-handoffs"
HANDOFF_CONSUMPTION_COLLECTION = "agent-handoff-consumptions"


class HandoffViewAuthorizer(Protocol):
    def can_view(self, principal_ref: str, handoff: AgentHandoff) -> bool: ...


class HandoffControlPlaneProjection:
    """Permission-aware direct projection used outside the registered Control Plane path."""

    def __init__(self, service: HandoffService, *, authorization: HandoffViewAuthorizer) -> None:
        self._service = service
        self._authorization = authorization

    def get_handoff(
        self,
        principal_ref: str,
        handoff_id: str,
        revision: int | None = None,
    ) -> dict[str, JsonValue]:
        handoff = self._service.get_handoff(handoff_id, revision)
        self._require_view(principal_ref, handoff)
        return handoff_projection(self._service, handoff)

    def list_task_handoffs(self, principal_ref: str, task_id: str) -> list[dict[str, JsonValue]]:
        projected: list[dict[str, JsonValue]] = []
        for handoff in self._service.list_handoffs_for_task(task_id):
            if self._authorization.can_view(principal_ref, handoff):
                projected.append(handoff_projection(self._service, handoff))
        return projected

    def list_step_handoffs(self, principal_ref: str, step_id: str) -> list[dict[str, JsonValue]]:
        projected: list[dict[str, JsonValue]] = []
        for handoff in self._service.list_handoffs_for_step(step_id):
            if self._authorization.can_view(principal_ref, handoff):
                projected.append(handoff_projection(self._service, handoff))
        return projected

    def _require_view(self, principal_ref: str, handoff: AgentHandoff) -> None:
        if not self._authorization.can_view(principal_ref, handoff):
            raise ContractError(ErrorCode.FORBIDDEN, "handoff is not visible to this principal")


class HandoffResourceService:
    """Task-scoped registered read surface for canonical Handoff history.

    The extension ControlPlane first applies collection-level #15 authorization. This service
    then applies the same owner/project-aware Task authorization used by other task-scoped
    domains before returning each Handoff. Unscoped enumeration remains safe because every
    candidate is individually filtered through the owning Task authorization boundary.
    """

    def __init__(self, control_plane: ControlPlane, service: HandoffService) -> None:
        self.control_plane = control_plane
        self.service = service

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        projected: list[dict[str, JsonValue]] = []
        for handoff in self._filtered(query):
            if await self._can_view(context, handoff, action="agent-handoff:list"):
                projected.append(handoff_projection(self.service, handoff))
        return tuple(projected)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        handoff_id, revision = _parse_handoff_resource_id(resource_id)
        handoff = self.service.get_handoff(handoff_id, revision)
        await self._require_view(context, handoff, action="agent-handoff:read")
        return handoff_projection(self.service, handoff)

    def _filtered(self, query: PageQuery) -> tuple[AgentHandoff, ...]:
        filters = query.filters or {}
        task_id = filters.get("task_id")
        step_id = filters.get("step_id")
        if task_id is not None and step_id is not None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "handoff history accepts task_id or step_id, not both",
            )
        if task_id is not None:
            return self.service.list_handoffs_for_task(task_id)
        if step_id is not None:
            return self.service.list_handoffs_for_step(step_id)
        return self.service.list_handoffs()

    async def _can_view(
        self,
        context: RequestContext,
        handoff: AgentHandoff,
        *,
        action: str,
    ) -> bool:
        task = await self.control_plane._kernel.get_task(handoff.task_id)
        return await self.control_plane._allowed_for_task(
            context,
            action,
            _handoff_resource_id(handoff),
            task,
        )

    async def _require_view(
        self,
        context: RequestContext,
        handoff: AgentHandoff,
        *,
        action: str,
    ) -> None:
        task = await self.control_plane._kernel.get_task(handoff.task_id)
        await self.control_plane._authorize_for_task(
            context,
            action,
            _handoff_resource_id(handoff),
            task,
        )


class HandoffConsumptionResourceService:
    """Task-scoped read-only history for durable HandoffConsumption evidence."""

    def __init__(self, control_plane: ControlPlane, service: HandoffService) -> None:
        self.control_plane = control_plane
        self.service = service
        self.handoffs = HandoffResourceService(control_plane, service)

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        filters = query.filters or {}
        handoff_id = filters.get("handoff_id")
        if handoff_id is not None:
            revision_value = filters.get("revision")
            try:
                revision = int(revision_value) if revision_value is not None else None
            except ValueError as exc:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "invalid Handoff consumption revision filter",
                ) from exc
            handoff = self.service.get_handoff(handoff_id, revision)
            if not await self.handoffs._can_view(
                context,
                handoff,
                action="agent-handoff-consumption:list",
            ):
                return ()
            return tuple(
                consumption_projection(item)
                for item in self.service.list_consumptions(handoff.handoff_id, handoff.revision)
            )

        resources: list[dict[str, JsonValue]] = []
        for handoff in self.handoffs._filtered(query):
            if not await self.handoffs._can_view(
                context,
                handoff,
                action="agent-handoff-consumption:list",
            ):
                continue
            resources.extend(
                consumption_projection(item)
                for item in self.service.list_consumptions(handoff.handoff_id, handoff.revision)
            )
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        handoff_id, revision, run_id = _parse_consumption_resource_id(resource_id)
        handoff = self.service.get_handoff(handoff_id, revision)
        await self.handoffs._require_view(
            context,
            handoff,
            action="agent-handoff-consumption:read",
        )
        for consumption in self.service.list_consumptions(handoff_id, revision):
            if consumption.consuming_run_id == run_id:
                return consumption_projection(consumption)
        raise ContractError(ErrorCode.NOT_FOUND, "Handoff consumption was not found")


def register_handoff_control_plane(control_plane: ControlPlane, service: HandoffService) -> None:
    """Register task-authorized Handoff evidence without mutation/workflow commands."""

    control_plane.register_resource_service(
        HANDOFF_COLLECTION,
        HandoffResourceService(control_plane, service),
    )
    control_plane.register_resource_service(
        HANDOFF_CONSUMPTION_COLLECTION,
        HandoffConsumptionResourceService(control_plane, service),
    )


def handoff_projection(service: HandoffService, handoff: AgentHandoff) -> dict[str, JsonValue]:
    content = handoff.content
    consumptions = service.list_consumptions(handoff.handoff_id, handoff.revision)
    projected: dict[str, JsonValue] = {
        "id": _handoff_resource_id(handoff),
        "type": "agent_handoff",
        "handoff_id": handoff.handoff_id,
        "revision": handoff.revision,
        "content_digest": handoff.content_digest,
        "created_at": handoff.created_at.isoformat(),
        "task_id": content.task_id,
        "plan_id": content.plan_id,
        "producer_step_id": content.producer_step_id,
        "consumer_step_id": content.consumer_step_id,
        "producer_run_id": content.producer_run_id,
        "producer": _project_participant(content.producer),
        "objective": content.objective,
        "completed_work_summary": content.completed_work_summary,
        "unresolved_questions": list(content.unresolved_questions),
        "blockers": list(content.blockers),
        "assumptions": list(content.assumptions),
        "constraints": list(content.constraints),
        "source_refs": [_project_source(ref) for ref in content.source_refs],
        "recommended_next_action": content.recommended_next_action,
        "requested_output": content.requested_output,
        "consumption_runs": [item.consuming_run_id for item in consumptions],
        "consumptions": [consumption_projection(item) for item in consumptions],
    }
    if content.intended_consumer is not None:
        projected["intended_consumer"] = _project_participant(content.intended_consumer)
    else:
        projected["consumer_requirements"] = list(content.consumer_requirements)
    if content.provenance is not None:
        projected["provenance"] = {
            "source": content.provenance.source,
            "actor_ref": content.provenance.actor_ref,
            "details": dict(content.provenance.details),
        }
    return projected


def consumption_projection(consumption: HandoffConsumption) -> dict[str, JsonValue]:
    projected: dict[str, JsonValue] = {
        "id": _consumption_resource_id(consumption),
        "type": "agent_handoff_consumption",
        "handoff_id": consumption.handoff_id,
        "handoff_revision": consumption.handoff_revision,
        "handoff_digest": consumption.handoff_digest,
        "consuming_run_id": consumption.consuming_run_id,
        "consumer": _project_participant(consumption.consumer),
        "consumed_at": consumption.consumed_at.isoformat(),
    }
    if consumption.context_bundle_ref is not None:
        projected["context_bundle_ref"] = _project_source(consumption.context_bundle_ref)
    return projected


def _project_participant(participant: ParticipantRef) -> dict[str, JsonValue]:
    kind, resource_id, revision = participant_key(participant)
    return {"kind": kind, "id": resource_id, "revision": revision}


def _project_source(reference: HandoffSourceRef) -> dict[str, JsonValue]:
    projected: dict[str, JsonValue] = {
        "kind": reference.kind.value,
        "resource_id": reference.resource_id,
    }
    if reference.revision is not None:
        projected["revision"] = reference.revision
    if reference.digest is not None:
        projected["digest"] = reference.digest
    return projected


def _handoff_resource_id(handoff: AgentHandoff) -> str:
    return f"{handoff.handoff_id}@{handoff.revision}"


def _consumption_resource_id(consumption: HandoffConsumption) -> str:
    return f"{consumption.handoff_id}@{consumption.handoff_revision}:{consumption.consuming_run_id}"


def _parse_handoff_resource_id(resource_id: str) -> tuple[str, int | None]:
    handoff_id, separator, revision = resource_id.partition("@")
    if not separator:
        return handoff_id, None
    try:
        return handoff_id, int(revision)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, "invalid Handoff resource revision") from exc


def _parse_consumption_resource_id(resource_id: str) -> tuple[str, int, str]:
    handoff_ref, separator, run_id = resource_id.partition(":")
    if not separator:
        raise ContractError(ErrorCode.INVALID_REQUEST, "invalid Handoff consumption resource ID")
    handoff_id, revision = _parse_handoff_resource_id(handoff_ref)
    if revision is None:
        raise ContractError(ErrorCode.INVALID_REQUEST, "Handoff consumption requires revision")
    return handoff_id, revision, run_id
