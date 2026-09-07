"""Control Plane resources and commands for optional Proposal/Specification governance."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane, ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import RiskClassification

from .models import Proposal, ProposalStatus, SpecificationRevision
from .service import GovernanceCallContext, GovernanceService

PROPOSAL_COLLECTION = "proposals"
SPECIFICATION_COLLECTION = "specifications"
PROPOSAL_REVISION_COLLECTION = "proposal-revisions"
SPECIFICATION_REVISION_COLLECTION = "specification-revisions"
GOVERNANCE_AUDIT_COLLECTION = "governance-events"

GOVERNANCE_COMMANDS = (
    "proposal.create",
    "proposal.revise",
    "proposal.dismiss",
    "proposal.supersede",
    "specification.create",
    "specification.revise",
    "specification.request-approval",
    "specification.convert-to-task",
)


class ProposalResourceService(ResourceService):
    def __init__(self, control_plane: ControlPlane, governance: GovernanceService) -> None:
        self._control_plane = control_plane
        self._governance = governance

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        visible: list[dict[str, JsonValue]] = []
        for proposal in self._governance.repository.list_proposals():
            if await _allowed(self._control_plane, context, "proposal:list", proposal):
                visible.append(proposal_resource(proposal))
        return tuple(visible)

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        return tuple(
            proposal_search_resource(value)
            for value in self._governance.repository.list_proposals()
        )

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        proposal = self._governance.repository.get_proposal(resource_id)
        await _require_allowed(self._control_plane, context, "proposal:read", proposal)
        return proposal_resource(proposal)

    async def search_result_allowed(self, context: RequestContext, resource_id: str) -> bool:
        try:
            proposal = self._governance.repository.get_proposal(resource_id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return False
            raise
        return await _allowed(self._control_plane, context, "proposal:read", proposal)


class SpecificationResourceService(ResourceService):
    def __init__(self, control_plane: ControlPlane, governance: GovernanceService) -> None:
        self._control_plane = control_plane
        self._governance = governance

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        visible: list[dict[str, JsonValue]] = []
        for specification in self._governance.repository.list_specifications():
            if await _allowed_spec(
                self._control_plane, context, "specification:list", specification
            ):
                visible.append(specification_resource(specification))
        return tuple(visible)

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        return tuple(
            specification_search_resource(value)
            for value in self._governance.repository.list_specifications()
        )

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        specification = self._governance.repository.get_specification(resource_id)
        await _require_allowed_spec(
            self._control_plane, context, "specification:read", specification
        )
        return specification_resource(specification)

    async def search_result_allowed(self, context: RequestContext, resource_id: str) -> bool:
        try:
            specification = self._governance.repository.get_specification(resource_id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return False
            raise
        return await _allowed_spec(
            self._control_plane, context, "specification:read", specification
        )


class ProposalRevisionResourceService(ResourceService):
    search_indexable = False

    def __init__(self, control_plane: ControlPlane, governance: GovernanceService) -> None:
        self._control_plane = control_plane
        self._governance = governance

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for current in self._governance.repository.list_proposals():
            if not await _allowed(self._control_plane, context, "proposal:read", current):
                continue
            resources.extend(
                proposal_revision_resource(value)
                for value in self._governance.repository.proposal_history(current.id)
            )
        return tuple(resources)

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        proposal_id, revision = _revision_ref(resource_id, "proposal")
        current = self._governance.repository.get_proposal(proposal_id)
        await _require_allowed(self._control_plane, context, "proposal:read", current)
        return proposal_revision_resource(
            self._governance.repository.get_proposal(proposal_id, revision)
        )


class SpecificationRevisionResourceService(ResourceService):
    search_indexable = False

    def __init__(self, control_plane: ControlPlane, governance: GovernanceService) -> None:
        self._control_plane = control_plane
        self._governance = governance

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for current in self._governance.repository.list_specifications():
            if not await _allowed_spec(self._control_plane, context, "specification:read", current):
                continue
            resources.extend(
                specification_revision_resource(value)
                for value in self._governance.repository.specification_history(current.id)
            )
        return tuple(resources)

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        specification_id, revision = _revision_ref(resource_id, "specification")
        current = self._governance.repository.get_specification(specification_id)
        await _require_allowed_spec(self._control_plane, context, "specification:read", current)
        return specification_revision_resource(
            self._governance.repository.get_specification(specification_id, revision)
        )


class GovernanceAuditResourceService(ResourceService):
    search_indexable = False

    def __init__(self, governance: GovernanceService) -> None:
        self._governance = governance

    async def list_resources(
        self, context: RequestContext, query: PageQuery
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_audit_resource(value) for value in self._governance.repository.list_audit())

    async def get_resource(self, context: RequestContext, resource_id: str) -> dict[str, JsonValue]:
        del context
        for event in self._governance.repository.list_audit():
            if event.id == resource_id:
                return _audit_resource(event)
        raise ContractError(ErrorCode.NOT_FOUND, "governance event was not found")


def register_governance_control_plane(
    control_plane: ControlPlane, governance: GovernanceService
) -> None:
    """Register governance as additive Control Plane resources; direct Task APIs remain intact."""

    control_plane.register_resource_service(
        PROPOSAL_COLLECTION, ProposalResourceService(control_plane, governance)
    )
    control_plane.register_resource_service(
        SPECIFICATION_COLLECTION, SpecificationResourceService(control_plane, governance)
    )
    control_plane.register_resource_service(
        PROPOSAL_REVISION_COLLECTION,
        ProposalRevisionResourceService(control_plane, governance),
    )
    control_plane.register_resource_service(
        SPECIFICATION_REVISION_COLLECTION,
        SpecificationRevisionResourceService(control_plane, governance),
    )
    control_plane.register_resource_service(
        GOVERNANCE_AUDIT_COLLECTION, GovernanceAuditResourceService(governance)
    )

    async def proposal_create(
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del resource_ref
        proposal = _proposal_from_payload(payload, context)
        await _require_allowed(control_plane, context, "proposal.create", proposal)
        return proposal_resource(governance.create_proposal(proposal, actor_ref=_actor(context)))

    async def proposal_revise(
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        current = governance.repository.get_proposal(resource_ref)
        await _require_allowed(control_plane, context, "proposal.revise", current)
        expected = _required_int(payload, "expected_revision")
        revised = _proposal_revision_from_payload(current, payload, expected)
        return proposal_resource(
            governance.revise_proposal(
                revised, expected_revision=expected, actor_ref=_actor(context)
            )
        )

    async def proposal_dismiss(
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        current = governance.repository.get_proposal(resource_ref)
        await _require_allowed(control_plane, context, "proposal.dismiss", current)
        expected = _required_int(payload, "expected_revision")
        return proposal_resource(
            governance.dismiss_proposal(
                resource_ref, expected_revision=expected, actor_ref=_actor(context)
            )
        )

    async def proposal_supersede(
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
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
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        del resource_ref
        specification = _specification_from_payload(payload, context)
        await _require_allowed_spec(control_plane, context, "specification.create", specification)
        return specification_resource(
            governance.create_specification(specification, actor_ref=_actor(context))
        )

    async def specification_revise(
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        current = governance.repository.get_specification(resource_ref)
        await _require_allowed_spec(control_plane, context, "specification.revise", current)
        expected = _required_int(payload, "expected_revision")
        revised = _specification_revision_from_payload(current, payload, expected)
        return specification_resource(
            governance.revise_specification(
                revised, expected_revision=expected, actor_ref=_actor(context)
            )
        )

    async def specification_request_approval(
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        if payload:
            raise ContractError(ErrorCode.INVALID_REQUEST, "approval request accepts no payload")
        current = governance.repository.get_specification(resource_ref)
        await _require_allowed_spec(
            control_plane, context, "specification.request-approval", current
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
        context: RequestContext, resource_ref: str, payload: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        current = governance.repository.get_specification(resource_ref)
        await _require_allowed_spec(
            control_plane, context, "specification.convert-to-task", current
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
        "proposal.dismiss": proposal_dismiss,
        "proposal.supersede": proposal_supersede,
        "specification.create": specification_create,
        "specification.revise": specification_revise,
        "specification.request-approval": specification_request_approval,
        "specification.convert-to-task": specification_convert,
    }
    for name, handler in handlers.items():
        control_plane.register_command(name, handler)


def proposal_resource(value: Proposal) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "type": "proposal",
        "title": value.title,
        "summary": value.summary,
        "reason": value.reason,
        "status": value.status.value,
        "revision": value.revision,
        "owner_ref": {"type": value.owner_ref.type, "id": value.owner_ref.id},
        "project_id": value.project_id,
        "workspace_id": value.workspace_id,
        "requester_ref": value.requester_ref,
        "source": value.source,
        "evidence_refs": list(value.evidence_refs),
        "confidence": value.confidence,
        "expected_value": value.expected_value,
        "risk": value.risk.value,
        "fingerprint": value.fingerprint,
        "supersedes_id": value.supersedes_id,
        "superseded_by_id": value.superseded_by_id,
        "converted_task_id": value.converted_task_id,
        "expires_at": value.expires_at.isoformat() if value.expires_at else None,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
    }


def proposal_search_resource(value: Proposal) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "type": "proposal",
        "title": value.title,
        "summary": value.summary[:500],
        "status": value.status.value,
        "revision": value.revision,
        "owner_ref": {"type": value.owner_ref.type, "id": value.owner_ref.id},
        "project_id": value.project_id,
        "workspace_id": value.workspace_id,
        "source": value.source,
        "risk": value.risk.value,
        "updated_at": value.updated_at.isoformat(),
    }


def specification_resource(value: SpecificationRevision) -> dict[str, JsonValue]:
    return {
        **specification_search_resource(value),
        "problem": value.problem,
        "scope": list(value.scope),
        "out_of_scope": list(value.out_of_scope),
        "acceptance_criteria": list(value.acceptance_criteria),
        "dependencies": list(value.dependencies),
        "constraints": list(value.constraints),
        "required_capabilities": list(value.required_capabilities),
        "model_requirements": dict(value.model_requirements),
        "agent_requirements": dict(value.agent_requirements),
        "data_security_constraints": list(value.data_security_constraints),
        "validation_strategy": list(value.validation_strategy),
        "required_tests": list(value.required_tests),
        "verification_requirements": list(value.verification_requirements),
        "required_human_gates": list(value.required_human_gates),
        "decomposition_hints": list(value.decomposition_hints),
        "assumptions": list(value.assumptions),
        "open_questions": list(value.open_questions),
        "provenance": dict(value.provenance),
    }


def specification_search_resource(value: SpecificationRevision) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "type": "specification",
        "title": value.goal[:160],
        "summary": value.goal[:500],
        "revision": value.revision,
        "version": str(value.revision),
        "content_digest": value.content_digest,
        "proposal_id": value.proposal_id,
        "goal_id": value.goal_id,
        "task_intake_id": value.task_intake_id,
        "owner_ref": {"type": value.owner_ref.type, "id": value.owner_ref.id},
        "project_id": value.project_id,
        "workspace_id": value.workspace_id,
        "requester_ref": value.requester_ref,
        "risk": value.risk.value,
        "approval_required": value.approval_required,
        "updated_at": value.created_at.isoformat(),
    }


def proposal_revision_resource(value: Proposal) -> dict[str, JsonValue]:
    resource = proposal_resource(value)
    resource["id"] = f"{value.id}:r{value.revision}"
    resource["type"] = "proposal-revision"
    resource["proposal_id"] = value.id
    return resource


def specification_revision_resource(value: SpecificationRevision) -> dict[str, JsonValue]:
    resource = specification_resource(value)
    resource["id"] = f"{value.id}:r{value.revision}"
    resource["type"] = "specification-revision"
    resource["specification_id"] = value.id
    return resource


def _proposal_from_payload(
    payload: dict[str, JsonValue],
    context: RequestContext,
    *,
    supersedes_id: str | None = None,
) -> Proposal:
    owner = _owner_from_payload(payload, context)
    return Proposal(
        id=_optional_string(payload, "id") or new_id("proposal"),
        title=_required_string(payload, "title"),
        summary=_required_string(payload, "summary"),
        reason=_required_string(payload, "reason"),
        owner_ref=owner,
        requester_ref=_actor(context),
        source=_optional_string(payload, "source") or "control-plane",
        status=ProposalStatus(_optional_string(payload, "status") or ProposalStatus.DRAFT.value),
        project_id=_optional_string(payload, "project_id"),
        workspace_id=_optional_string(payload, "workspace_id"),
        evidence_refs=_string_tuple(payload, "evidence_refs"),
        confidence=_optional_number(payload, "confidence"),
        expected_value=_optional_number(payload, "expected_value"),
        risk=RiskClassification(
            _optional_string(payload, "risk") or RiskClassification.STANDARD.value
        ),
        fingerprint=_optional_string(payload, "fingerprint"),
        supersedes_id=supersedes_id,
        provenance={"source": "control-plane", "request_id": context.request_id},
    )


def _proposal_revision_from_payload(
    current: Proposal, payload: dict[str, JsonValue], expected_revision: int
) -> Proposal:
    return replace(
        current,
        title=_optional_string(payload, "title") or current.title,
        summary=_optional_string(payload, "summary") or current.summary,
        reason=_optional_string(payload, "reason") or current.reason,
        status=ProposalStatus(_optional_string(payload, "status") or current.status.value),
        evidence_refs=(
            _string_tuple(payload, "evidence_refs")
            if "evidence_refs" in payload
            else current.evidence_refs
        ),
        confidence=(
            _optional_number(payload, "confidence")
            if "confidence" in payload
            else current.confidence
        ),
        expected_value=(
            _optional_number(payload, "expected_value")
            if "expected_value" in payload
            else current.expected_value
        ),
        risk=RiskClassification(_optional_string(payload, "risk") or current.risk.value),
        fingerprint=(
            _optional_string(payload, "fingerprint")
            if "fingerprint" in payload
            else current.fingerprint
        ),
        revision=expected_revision + 1,
        updated_at=datetime.now(UTC),
    )


def _specification_from_payload(
    payload: dict[str, JsonValue], context: RequestContext
) -> SpecificationRevision:
    return SpecificationRevision(
        id=_optional_string(payload, "id") or new_id("specification"),
        proposal_id=_optional_string(payload, "proposal_id"),
        goal_id=_optional_string(payload, "goal_id"),
        task_intake_id=_optional_string(payload, "task_intake_id"),
        project_id=_optional_string(payload, "project_id"),
        workspace_id=_optional_string(payload, "workspace_id"),
        problem=_required_string(payload, "problem"),
        goal=_required_string(payload, "goal"),
        scope=_required_string_tuple(payload, "scope"),
        out_of_scope=_string_tuple(payload, "out_of_scope"),
        acceptance_criteria=_required_string_tuple(payload, "acceptance_criteria"),
        dependencies=_string_tuple(payload, "dependencies"),
        constraints=_string_tuple(payload, "constraints"),
        risk=RiskClassification(
            _optional_string(payload, "risk") or RiskClassification.STANDARD.value
        ),
        required_capabilities=_string_tuple(payload, "required_capabilities"),
        model_requirements=_json_mapping(payload, "model_requirements"),
        agent_requirements=_json_mapping(payload, "agent_requirements"),
        data_security_constraints=_string_tuple(payload, "data_security_constraints"),
        validation_strategy=_string_tuple(payload, "validation_strategy"),
        required_tests=_string_tuple(payload, "required_tests"),
        verification_requirements=_string_tuple(payload, "verification_requirements"),
        required_human_gates=_string_tuple(payload, "required_human_gates"),
        decomposition_hints=_string_tuple(payload, "decomposition_hints"),
        assumptions=_string_tuple(payload, "assumptions"),
        open_questions=_string_tuple(payload, "open_questions"),
        owner_ref=_owner_from_payload(payload, context),
        requester_ref=_actor(context),
        provenance={"source": "control-plane", "request_id": context.request_id},
    )


def _specification_revision_from_payload(
    current: SpecificationRevision, payload: dict[str, JsonValue], expected_revision: int
) -> SpecificationRevision:
    mutable = {
        "problem": _optional_string(payload, "problem") or current.problem,
        "goal": _optional_string(payload, "goal") or current.goal,
        "scope": _string_tuple(payload, "scope") if "scope" in payload else current.scope,
        "out_of_scope": _string_tuple(payload, "out_of_scope")
        if "out_of_scope" in payload
        else current.out_of_scope,
        "acceptance_criteria": _string_tuple(payload, "acceptance_criteria")
        if "acceptance_criteria" in payload
        else current.acceptance_criteria,
        "dependencies": _string_tuple(payload, "dependencies")
        if "dependencies" in payload
        else current.dependencies,
        "constraints": _string_tuple(payload, "constraints")
        if "constraints" in payload
        else current.constraints,
        "risk": RiskClassification(_optional_string(payload, "risk") or current.risk.value),
        "required_capabilities": _string_tuple(payload, "required_capabilities")
        if "required_capabilities" in payload
        else current.required_capabilities,
        "model_requirements": _json_mapping(payload, "model_requirements")
        if "model_requirements" in payload
        else current.model_requirements,
        "agent_requirements": _json_mapping(payload, "agent_requirements")
        if "agent_requirements" in payload
        else current.agent_requirements,
        "data_security_constraints": _string_tuple(payload, "data_security_constraints")
        if "data_security_constraints" in payload
        else current.data_security_constraints,
        "validation_strategy": _string_tuple(payload, "validation_strategy")
        if "validation_strategy" in payload
        else current.validation_strategy,
        "required_tests": _string_tuple(payload, "required_tests")
        if "required_tests" in payload
        else current.required_tests,
        "verification_requirements": _string_tuple(payload, "verification_requirements")
        if "verification_requirements" in payload
        else current.verification_requirements,
        "required_human_gates": _string_tuple(payload, "required_human_gates")
        if "required_human_gates" in payload
        else current.required_human_gates,
        "decomposition_hints": _string_tuple(payload, "decomposition_hints")
        if "decomposition_hints" in payload
        else current.decomposition_hints,
        "assumptions": _string_tuple(payload, "assumptions")
        if "assumptions" in payload
        else current.assumptions,
        "open_questions": _string_tuple(payload, "open_questions")
        if "open_questions" in payload
        else current.open_questions,
    }
    return replace(current, revision=expected_revision + 1, content_digest="", **mutable)


def _owner_from_payload(payload: Mapping[str, JsonValue], context: RequestContext) -> OwnerRef:
    raw = payload.get("owner_ref")
    if isinstance(raw, Mapping):
        owner_type = raw.get("type")
        owner_id = raw.get("id")
        if owner_type in {"user", "organization", "team", "service"} and isinstance(owner_id, str):
            return OwnerRef(type=cast("object", owner_type), id=owner_id)  # type: ignore[arg-type]
    if context.actor.owner_type is None or context.actor.owner_id is None:
        raise ContractError(ErrorCode.INVALID_REQUEST, "owner_ref is required")
    return OwnerRef(type=context.actor.owner_type, id=context.actor.owner_id)


def _call_context(context: RequestContext) -> GovernanceCallContext:
    return GovernanceCallContext(
        actor_ref=_actor(context),
        correlation_id=context.correlation_id,
        idempotency_key=context.idempotency_key,
    )


def _actor(context: RequestContext) -> str:
    return context.actor.principal_ref


async def _allowed(
    control_plane: ControlPlane,
    context: RequestContext,
    action: str,
    value: Proposal,
) -> bool:
    return await control_plane._allowed(
        context,
        action,
        value.id,
        owner_type=value.owner_ref.type,
        owner_id=value.owner_ref.id,
        project_id=value.project_id,
    )


async def _allowed_spec(
    control_plane: ControlPlane,
    context: RequestContext,
    action: str,
    value: SpecificationRevision,
) -> bool:
    return await control_plane._allowed(
        context,
        action,
        value.id,
        owner_type=value.owner_ref.type,
        owner_id=value.owner_ref.id,
        project_id=value.project_id,
    )


async def _require_allowed(
    control_plane: ControlPlane, context: RequestContext, action: str, value: Proposal
) -> None:
    if not await _allowed(control_plane, context, action, value):
        raise ContractError(ErrorCode.FORBIDDEN, "proposal operation is forbidden")


async def _require_allowed_spec(
    control_plane: ControlPlane,
    context: RequestContext,
    action: str,
    value: SpecificationRevision,
) -> None:
    if not await _allowed_spec(control_plane, context, action, value):
        raise ContractError(ErrorCode.FORBIDDEN, "specification operation is forbidden")


def _revision_ref(value: str, prefix: str) -> tuple[str, int]:
    marker = value.rfind(":r")
    if marker <= 0:
        raise ContractError(ErrorCode.INVALID_REQUEST, "revision resource id is invalid")
    resource_id = value[:marker]
    try:
        revision = int(value[marker + 2 :])
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, "revision resource id is invalid") from exc
    if not resource_id.startswith(f"{prefix}_") or revision < 1:
        raise ContractError(ErrorCode.INVALID_REQUEST, "revision resource id is invalid")
    return resource_id, revision


def _audit_resource(value: object) -> dict[str, JsonValue]:
    from .models import GovernanceAuditEvent

    if not isinstance(value, GovernanceAuditEvent):
        raise TypeError("expected GovernanceAuditEvent")
    return {
        "id": value.id,
        "type": "governance-event",
        "event_type": value.event_type,
        "resource_type": value.resource_type,
        "resource_id": value.resource_id,
        "actor_ref": value.actor_ref,
        "project_id": value.project_id,
        "revision": value.revision,
        "digest": value.digest,
        "metadata": dict(value.metadata),
        "occurred_at": value.occurred_at.isoformat(),
    }


def _required_string(payload: Mapping[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _optional_string(payload: Mapping[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a string")
    return value


def _required_int(payload: Mapping[str, JsonValue], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be an integer")
    return value


def _string_tuple(payload: Mapping[str, JsonValue], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be an array of strings")
    result = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in result):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must contain non-blank strings")
    return cast(tuple[str, ...], result)


def _required_string_tuple(payload: Mapping[str, JsonValue], key: str) -> tuple[str, ...]:
    value = _string_tuple(payload, key)
    if not value:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must not be empty")
    return value


def _optional_number(payload: Mapping[str, JsonValue], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be numeric")
    return float(value)


def _json_mapping(payload: Mapping[str, JsonValue], key: str) -> Mapping[str, JsonValue]:
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be an object")
    return cast(Mapping[str, JsonValue], value)


def _required_mapping(payload: Mapping[str, JsonValue], key: str) -> Mapping[str, JsonValue]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be an object")
    return cast(Mapping[str, JsonValue], value)
