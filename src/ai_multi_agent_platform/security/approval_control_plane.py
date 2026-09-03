"""Read-only Control Plane projection for canonical #15 approvals."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .approvals import ApprovalRecord, ApprovalService

APPROVAL_COLLECTION = "approvals"


class ApprovalResourceService:
    """Expose approval lifecycle metadata without exposing proposed payload values."""

    def __init__(self, approvals: ApprovalService) -> None:
        self._approvals = approvals

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_approval_resource(record) for record in self._approvals.all())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return _approval_resource(self._approvals.get(resource_id))


def approval_resource_services(
    approvals: ApprovalService,
) -> dict[str, ApprovalResourceService]:
    """Register the canonical approval inspection collection through the extension seam."""

    return {APPROVAL_COLLECTION: ApprovalResourceService(approvals)}


def _approval_resource(record: ApprovalRecord) -> dict[str, JsonValue]:
    decision_by: JsonValue = None
    if record.decision_by is not None:
        decision_by = {
            "type": record.decision_by.type,
            "id": record.decision_by.id,
        }

    return {
        "id": record.approval_id,
        "type": "approval",
        "status": record.status.value,
        "subject_type": record.approval.subject_type,
        "subject_id": record.approval.subject_id,
        "owner_ref": {
            "type": record.approval.owner_ref.type,
            "id": record.approval.owner_ref.id,
        },
        "requester_ref": record.requester_ref,
        "action": record.action,
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "requested_action_digest": record.requested_action_digest,
        "risk": record.risk.value,
        "policy_id": record.policy_id,
        "reason": record.reason,
        "project_id": record.project_id,
        "task_id": record.task_id,
        "run_id": record.run_id,
        "capability_ref": record.capability_ref,
        "payload_ref": record.payload_ref,
        "created_at": record.created_at.isoformat(),
        "expires_at": record.expires_at.isoformat(),
        "decision_by": decision_by,
        "decision_at": record.decision_at.isoformat() if record.decision_at is not None else None,
        "decision_comment": record.decision_comment,
    }
