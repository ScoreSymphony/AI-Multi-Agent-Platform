"""Permission-aware read-only Control Plane projection for Agent Handoff history."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import AgentHandoff, HandoffSourceRef, ParticipantRef, participant_key
from .service import HandoffService


class HandoffViewAuthorizer(Protocol):
    def can_view(self, principal_ref: str, handoff: AgentHandoff) -> bool: ...


class HandoffControlPlaneProjection:
    """Expose safe Handoff history without adding mutation or workflow authority."""

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
        return self._project(handoff)

    def list_task_handoffs(
        self, principal_ref: str, task_id: str
    ) -> list[dict[str, JsonValue]]:
        projected: list[dict[str, JsonValue]] = []
        for handoff in self._service.list_handoffs_for_task(task_id):
            if self._authorization.can_view(principal_ref, handoff):
                projected.append(self._project(handoff))
        return projected

    def list_step_handoffs(
        self, principal_ref: str, step_id: str
    ) -> list[dict[str, JsonValue]]:
        projected: list[dict[str, JsonValue]] = []
        for handoff in self._service.list_handoffs_for_step(step_id):
            if self._authorization.can_view(principal_ref, handoff):
                projected.append(self._project(handoff))
        return projected

    def _require_view(self, principal_ref: str, handoff: AgentHandoff) -> None:
        if not self._authorization.can_view(principal_ref, handoff):
            raise ContractError(ErrorCode.FORBIDDEN, "handoff is not visible to this principal")

    def _project(self, handoff: AgentHandoff) -> dict[str, JsonValue]:
        content = handoff.content
        consumptions = self._service.list_consumptions(handoff.handoff_id, handoff.revision)
        projected: dict[str, JsonValue] = {
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
        }
        if content.intended_consumer is not None:
            projected["intended_consumer"] = _project_participant(content.intended_consumer)
        else:
            projected["consumer_requirements"] = list(content.consumer_requirements)
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
