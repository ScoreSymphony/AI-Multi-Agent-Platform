"""Explicit Control Plane ownership adapter for optional Governance state (#982)."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane, ControlPlaneModule
from ai_multi_agent_platform.control_plane.models import RequestContext

from .control_plane import (
    GOVERNANCE_AUDIT_COLLECTION,
    GOVERNANCE_COMMANDS,
    PROPOSAL_COLLECTION,
    PROPOSAL_REVISION_COLLECTION,
    SPECIFICATION_COLLECTION,
    SPECIFICATION_REVISION_COLLECTION,
    GovernanceAuditResourceService,
    ProposalResourceService,
    ProposalRevisionResourceService,
    SpecificationResourceService,
    SpecificationRevisionResourceService,
    _actor,
    _call_context,
    _optional_string,
    _proposal_from_payload,
    _proposal_revision_from_payload,
    _require_allowed,
    _require_allowed_spec,
    _required_int,
    _required_mapping,
    _specification_from_payload,
    _specification_revision_from_payload,
    proposal_resource,
    specification_resource,
)
from .service import GovernanceService

GOVERNANCE_MODULE = "governance"


def governance_control_plane_module(
    control_plane: ControlPlane,
    governance: GovernanceService,
) -> ControlPlaneModule:
    """Build the complete northbound Governance contribution with one named owner."""

    async def proposal_create(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del resource_ref
        proposal = _proposal_from_payload(payload, context)
        await _require_allowed(control_plane, context, "proposal.create", proposal)
        return proposal_resource(governance.create_proposal(proposal, actor_ref=_actor(context)))

    async def proposal_revise(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current = governance.repository.get_proposal(resource_ref)
        await _require_allowed(control_plane, context, "proposal.revise", current)
        expected = _required_int(payload, "expected_revision")
        revised = _proposal_revision_from_payload(current, payload, expected)
        return proposal_resource(
            governance.revise_proposal(
                revised,
                expected_revision=expected,
                actor_ref=_actor(context),
            )
        )

    async def proposal_request_clarification(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current = governance.repository.get_proposal(resource_ref)
        await _require_allowed(
            control_plane,
            context,
            "proposal.request-clarification",
            current,
        )
        expected = _required_int(payload, "expected_revision")
        return proposal_resource(
            governance.request_clarification(
                resource_ref,
                expected_revision=expected,
                actor_ref=_actor(context),
            )
        )

    async def proposal_dismiss(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current = governance.repository.get_proposal(resource_ref)
        await _require_allowed(control_plane, context, "proposal.dismiss", current)
        expected = _required_int(payload, "expected_revision")
        return proposal_resource(
            governance.dismiss_proposal(
                resource_ref,
                expected_revision=expected,
                actor_ref=_actor(context),
            )
        )

    async def proposal_supersede(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current = governance.repository.get_proposal(resource_ref)
        await _require_allowed(control_plane, context, "proposal.supersede", current)
        expected = _required_int(payload, "expected_revision")
        replacement_payload = _required_mapping(payload, "replacement")
        replacement = _proposal_from_payload(
            cast(dict[str, JsonValue], dict(replacement_payload)),
            context,
            supersedes_id=resource_ref,
        )
        await _require_allowed(control_plane, context, "proposal.create", replacement)
        superseded, created = governance.supersede_proposal(
            resource_ref,
            replacement,
            expected_revision=expected,
            actor_ref=_actor(context),
        )
        return {
            "proposal": proposal_resource(superseded),
            "replacement": proposal_resource(created),
        }

    async def specification_create(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del resource_ref
        specification = _specification_from_payload(payload, context)
        await _require_allowed_spec(
            control_plane,
            context,
            "specification.create",
            specification,
        )
        return specification_resource(
            governance.create_specification(specification, actor_ref=_actor(context))
        )

    async def specification_revise(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current = governance.repository.get_specification(resource_ref)
        await _require_allowed_spec(control_plane, context, "specification.revise", current)
        expected = _required_int(payload, "expected_revision")
        revised = _specification_revision_from_payload(current, payload, expected)
        return specification_resource(
            governance.revise_specification(
                revised,
                expected_revision=expected,
                actor_ref=_actor(context),
            )
        )

    async def specification_request_approval(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if payload:
            raise ContractError(ErrorCode.INVALID_REQUEST, "approval request accepts no payload")
        current = governance.repository.get_specification(resource_ref)
        await _require_allowed_spec(
            control_plane,
            context,
            "specification.request-approval",
            current,
        )
        approval = await governance.request_approval(resource_ref, context=_call_context(context))
        return {
            "id": approval.approval_id,
            "type": "approval",
            "status": approval.status.value,
            "specification_id": resource_ref,
            "specification_revision": current.revision,
            "specification_digest": current.content_digest,
            "expires_at": approval.expires_at.isoformat(),
        }

    async def specification_convert(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current = governance.repository.get_specification(resource_ref)
        await _require_allowed_spec(
            control_plane,
            context,
            "specification.convert-to-task",
            current,
        )
        approval_id = _optional_string(payload, "approval_id")
        unknown = set(payload) - {"approval_id"}
        if unknown:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"unsupported conversion fields: {sorted(unknown)!r}",
            )
        task = await governance.convert_to_task(
            resource_ref,
            context=replace(_call_context(context), approval_id=approval_id),
        )
        return {
            "id": task.task_id,
            "type": "task",
            "status": task.status.value,
            "project_id": task.task.project_id,
            "governance": cast(JsonValue, dict(task.task.metadata).get("governance")),
        }

    handlers = {
        "proposal.create": proposal_create,
        "proposal.revise": proposal_revise,
        "proposal.request-clarification": proposal_request_clarification,
        "proposal.dismiss": proposal_dismiss,
        "proposal.supersede": proposal_supersede,
        "specification.create": specification_create,
        "specification.revise": specification_revise,
        "specification.request-approval": specification_request_approval,
        "specification.convert-to-task": specification_convert,
    }
    if frozenset(handlers) != frozenset(GOVERNANCE_COMMANDS):
        raise RuntimeError("explicit Governance module command inventory is incomplete")

    return ControlPlaneModule(
        name=GOVERNANCE_MODULE,
        resource_services={
            PROPOSAL_COLLECTION: ProposalResourceService(control_plane, governance),
            SPECIFICATION_COLLECTION: SpecificationResourceService(control_plane, governance),
            PROPOSAL_REVISION_COLLECTION: ProposalRevisionResourceService(
                control_plane,
                governance,
            ),
            SPECIFICATION_REVISION_COLLECTION: SpecificationRevisionResourceService(
                control_plane,
                governance,
            ),
            GOVERNANCE_AUDIT_COLLECTION: GovernanceAuditResourceService(
                control_plane,
                governance,
            ),
        },
        command_handlers=handlers,
    )


__all__ = ["GOVERNANCE_MODULE", "governance_control_plane_module"]
