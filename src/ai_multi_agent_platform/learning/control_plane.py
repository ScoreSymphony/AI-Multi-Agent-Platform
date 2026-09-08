"""Control Plane inspection and decision surfaces for governed learning (#595)."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue, OperationContext
from ai_multi_agent_platform.control_plane.extensions import ControlPlane, ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    RiskClassification,
    infer_actor_identity,
)
from ai_multi_agent_platform.security.approvals import ApprovalRecord
from ai_multi_agent_platform.security.redaction import redact_sensitive

from .models import (
    FeedbackRecord,
    FeedbackType,
    LearningCandidate,
    LearningGatePlan,
    LearningReference,
    LearningSourceType,
    LearningTarget,
    LearningTargetType,
    candidate_to_dict,
    feedback_to_dict,
)
from .service import LearningService

LEARNING_CANDIDATE_COLLECTION = "learning-candidates"
LEARNING_FEEDBACK_COLLECTION = "learning-feedback"
LEARNING_COLLECTIONS = (
    LEARNING_CANDIDATE_COLLECTION,
    LEARNING_FEEDBACK_COLLECTION,
)
LEARNING_COMMANDS = (
    "learning.feedback.create",
    "learning.propose",
    "learning.propose-from-feedback",
    "learning.evidence",
    "learning.accept",
    "learning.reject",
    "learning.supersede",
    "learning.promote",
)


class LearningCandidateResourceService(ResourceService):
    """Expose current candidates together with append-only revision and Approval history."""

    def __init__(self, learning: LearningService) -> None:
        self._learning = learning

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        return tuple(
            _candidate_resource(self._learning, candidate)
            for candidate in self._learning.list_candidates()
            if _candidate_visible(candidate, context)
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        candidate = self._learning.get_candidate(resource_id)
        _require_candidate_visible(candidate, context)
        return _candidate_resource(self._learning, candidate)


class LearningFeedbackResourceService(ResourceService):
    """Expose immutable canonical feedback without converting comments into authority."""

    def __init__(self, learning: LearningService) -> None:
        self._learning = learning

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        return tuple(
            _feedback_resource(feedback)
            for feedback in self._learning.repository.list_feedback()
            if _project_visible(feedback.project_id, context)
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        feedback = self._learning.repository.get_feedback(resource_id)
        if not _project_visible(feedback.project_id, context):
            raise ContractError(ErrorCode.NOT_FOUND, "Learning feedback was not found")
        return _feedback_resource(feedback)


def register_learning_control_plane(control_plane: ControlPlane, learning: LearningService) -> None:
    """Register governed Learning resources and lifecycle commands on the canonical API."""

    control_plane.register_resource_service(
        LEARNING_CANDIDATE_COLLECTION,
        LearningCandidateResourceService(learning),
    )
    control_plane.register_resource_service(
        LEARNING_FEEDBACK_COLLECTION,
        LearningFeedbackResourceService(learning),
    )

    async def create_feedback(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, LEARNING_FEEDBACK_COLLECTION)
        feedback, _ = learning.record_feedback(
            feedback_type=_enum(FeedbackType, payload, "feedback_type"),
            subject=_reference(_required_object(payload, "subject")),
            creator_ref=context.actor.principal_ref,
            comment=_optional_string(payload.get("comment"), "comment"),
            target=_optional_target(payload.get("target")),
            project_id=_optional_string(payload.get("project_id"), "project_id"),
        )
        return _feedback_resource(feedback)

    async def propose(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_collection(resource_ref, LEARNING_CANDIDATE_COLLECTION)
        source_refs = _reference_tuple(payload.get("source_refs"), "source_refs")
        if not source_refs:
            source_refs = (
                LearningReference(
                    kind="control_plane_operator_proposal",
                    resource_id=_require_idempotency_key(context),
                ),
            )
        candidate, _ = learning.create_candidate(
            source_type=LearningSourceType.OPERATOR_PROPOSAL,
            problem=_required_string(payload, "problem"),
            target=_target(_required_object(payload, "target")),
            improvement_type=_required_string(payload, "improvement_type"),
            expected_benefit=_required_string(payload, "expected_benefit"),
            risk=_enum(RiskClassification, payload, "risk"),
            gate_plan=_gate_plan(_required_object(payload, "gate_plan")),
            creator_ref=context.actor.principal_ref,
            source_refs=source_refs,
            evidence_refs=_reference_tuple(payload.get("evidence_refs"), "evidence_refs"),
            proposed_change=_json_object(payload.get("proposed_change"), "proposed_change"),
            proposed_artifact_ref=_optional_reference(payload.get("proposed_artifact_ref")),
            project_id=_optional_string(payload.get("project_id"), "project_id"),
        )
        return _candidate_resource(learning, candidate)

    async def propose_from_feedback(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        feedback = learning.repository.get_feedback(resource_ref)
        if not _project_visible(feedback.project_id, context):
            raise ContractError(ErrorCode.NOT_FOUND, "Learning feedback was not found")
        candidate, _ = learning.create_from_feedback(
            feedback,
            problem=_required_string(payload, "problem"),
            target=_target(_required_object(payload, "target")),
            improvement_type=_required_string(payload, "improvement_type"),
            expected_benefit=_required_string(payload, "expected_benefit"),
            risk=_enum(RiskClassification, payload, "risk"),
            gate_plan=_gate_plan(_required_object(payload, "gate_plan")),
            creator_ref=context.actor.principal_ref,
            proposed_change=_json_object(payload.get("proposed_change"), "proposed_change"),
            proposed_artifact_ref=_optional_reference(payload.get("proposed_artifact_ref")),
            evidence_refs=_reference_tuple(payload.get("evidence_refs"), "evidence_refs"),
        )
        return _candidate_resource(learning, candidate)

    async def record_evidence(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        candidate = learning.get_candidate(resource_ref)
        _require_candidate_visible(candidate, context)
        updated = learning.record_gate_evidence(
            resource_ref,
            evaluation_run_ids=_string_tuple(
                payload.get("evaluation_run_ids"),
                "evaluation_run_ids",
            ),
            verification_ids=_string_tuple(
                payload.get("verification_ids"),
                "verification_ids",
            ),
            expected_revision=_required_positive_int(payload, "expected_revision"),
        )
        return _candidate_resource(learning, updated)

    async def accept(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_candidate_visible(learning.get_candidate(resource_ref), context)
        updated = learning.accept(
            resource_ref,
            expected_revision=_required_positive_int(payload, "expected_revision"),
        )
        return _candidate_resource(learning, updated)

    async def reject(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_candidate_visible(learning.get_candidate(resource_ref), context)
        updated = learning.reject(
            resource_ref,
            expected_revision=_required_positive_int(payload, "expected_revision"),
        )
        return _candidate_resource(learning, updated)

    async def supersede(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_candidate_visible(learning.get_candidate(resource_ref), context)
        replacement_id = _required_string(payload, "superseded_by")
        _require_candidate_visible(learning.get_candidate(replacement_id), context)
        updated = learning.supersede(
            resource_ref,
            superseded_by=replacement_id,
            expected_revision=_required_positive_int(payload, "expected_revision"),
        )
        return _candidate_resource(learning, updated)

    async def promote(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        candidate = learning.get_candidate(resource_ref)
        _require_candidate_visible(candidate, context)
        updated = await learning.promote(
            resource_ref,
            actor=_actor(context),
            operation=_operation(context, candidate.project_id),
            approval_id=_optional_string(payload.get("approval_id"), "approval_id"),
            automatic=False,
            expected_revision=_required_positive_int(payload, "expected_revision"),
        )
        return _candidate_resource(learning, updated)

    handlers = {
        "learning.feedback.create": create_feedback,
        "learning.propose": propose,
        "learning.propose-from-feedback": propose_from_feedback,
        "learning.evidence": record_evidence,
        "learning.accept": accept,
        "learning.reject": reject,
        "learning.supersede": supersede,
        "learning.promote": promote,
    }
    for command, handler in handlers.items():
        control_plane.register_command(command, handler)


def _candidate_resource(
    learning: LearningService,
    candidate: LearningCandidate,
) -> dict[str, JsonValue]:
    payload = candidate_to_dict(candidate)
    payload["id"] = candidate.learning_candidate_id
    payload["type"] = "learning-candidate"
    payload["history"] = [
        _candidate_history_entry(learning.get_candidate(candidate.learning_candidate_id, revision))
        for revision in range(1, candidate.revision + 1)
    ]
    payload["approvals"] = [
        _approval_resource(record)
        for record in learning.authorization_gate.approvals.all()
        if record.payload_ref is not None
        and record.payload_ref.startswith(f"{candidate.learning_candidate_id}@r")
    ]
    payload["post_promotion_regression_status"] = _post_promotion_regression_status(candidate)
    redacted = redact_sensitive(payload)
    if not isinstance(redacted, dict):
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            "Learning candidate redaction returned an invalid projection",
        )
    return cast(dict[str, JsonValue], redacted)


def _candidate_history_entry(candidate: LearningCandidate) -> dict[str, JsonValue]:
    return {
        "revision": candidate.revision,
        "status": candidate.status.value,
        "content_digest": candidate.content_digest,
        "evaluation_run_ids": list(candidate.evaluation_run_ids),
        "verification_ids": list(candidate.verification_ids),
        "superseded_by": candidate.superseded_by,
        "promotion": candidate_to_dict(candidate)["promotion"],
        "updated_at": candidate.updated_at.isoformat(),
    }


def _approval_resource(record: ApprovalRecord) -> dict[str, JsonValue]:
    approval_id = record.approval_id
    status = record.status
    decision_at = record.decision_at
    return {
        "approval_id": str(approval_id),
        "status": str(status.value),
        "requested_action_digest": str(record.requested_action_digest),
        "risk": str(record.risk.value),
        "policy_id": str(record.policy_id),
        "payload_ref": cast(str | None, record.payload_ref),
        "created_at": record.created_at.isoformat(),
        "expires_at": record.expires_at.isoformat(),
        "decision_at": None if decision_at is None else decision_at.isoformat(),
    }


def _feedback_resource(feedback: FeedbackRecord) -> dict[str, JsonValue]:
    payload = feedback_to_dict(feedback)
    payload["id"] = str(feedback.feedback_id)
    payload["type"] = "learning-feedback"
    redacted = redact_sensitive(payload)
    if not isinstance(redacted, dict):
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            "Learning feedback redaction returned an invalid projection",
        )
    return cast(dict[str, JsonValue], redacted)


def _post_promotion_regression_status(candidate: LearningCandidate) -> str:
    if candidate.status.value != "promoted":
        return "not_applicable"
    if any(item.kind == "post_promotion_evaluation_run" for item in candidate.evidence_refs):
        return "recorded"
    return "not_recorded"


def _candidate_visible(candidate: LearningCandidate, context: RequestContext) -> bool:
    return _project_visible(candidate.project_id, context)


def _require_candidate_visible(candidate: LearningCandidate, context: RequestContext) -> None:
    if not _candidate_visible(candidate, context):
        raise ContractError(ErrorCode.NOT_FOUND, "Learning Candidate was not found")


def _project_visible(project_id: str | None, context: RequestContext) -> bool:
    del project_id, context
    return True


def _actor(context: RequestContext) -> ActorIdentity:
    if context.actor.actor_type is None:
        return infer_actor_identity(context.actor.principal_ref)
    try:
        return ActorIdentity(
            context.actor.principal_ref,
            ActorType(context.actor.actor_type),
        )
    except ValueError as exc:
        raise ContractError(
            ErrorCode.UNAUTHORIZED,
            "authenticated actor type is not recognized by Learning security",
        ) from exc


def _operation(context: RequestContext, project_id: str | None) -> OperationContext:
    return OperationContext(
        correlation_id=context.correlation_id,
        owner_type=context.actor.owner_type,
        owner_id=context.actor.owner_id,
        project_id=project_id,
    )


def _require_collection(resource_ref: str, expected: str) -> None:
    if resource_ref != expected:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"resource_ref must be {expected!r}",
        )


def _require_idempotency_key(context: RequestContext) -> str:
    if context.idempotency_key is None:
        raise ContractError(ErrorCode.INVALID_REQUEST, "Idempotency-Key is required")
    return context.idempotency_key


def _required_string(payload: Mapping[str, JsonValue], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field_name} must be a non-blank string",
        )
    return value


def _optional_string(value: JsonValue, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field_name} must be a non-blank string",
        )
    return value


def _required_positive_int(payload: Mapping[str, JsonValue], field_name: str) -> int:
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field_name} must be a positive integer",
        )
    return value


def _optional_bool(value: JsonValue, field_name: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be a boolean")
    return value


def _required_object(
    payload: Mapping[str, JsonValue],
    field_name: str,
) -> dict[str, JsonValue]:
    if field_name not in payload:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} is required")
    return _json_object(payload[field_name], field_name)


def _json_object(value: JsonValue, field_name: str) -> dict[str, JsonValue]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be an object")
    return value


def _string_tuple(value: JsonValue, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be an array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"{field_name} must contain only non-blank strings",
            )
        result.append(item)
    return tuple(result)


def _reference_tuple(value: JsonValue, field_name: str) -> tuple[LearningReference, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be an array")
    return tuple(_reference(_object(item, field_name)) for item in value)


def _reference(payload: Mapping[str, JsonValue]) -> LearningReference:
    revision = payload.get("revision")
    if revision is not None and not isinstance(revision, str | int):
        raise ContractError(ErrorCode.INVALID_REQUEST, "reference revision must be string/integer")
    return LearningReference(
        kind=_required_string(payload, "kind"),
        resource_id=_required_string(payload, "resource_id"),
        revision=None if revision is None else str(revision),
        digest=_optional_string(payload.get("digest"), "digest"),
    )


def _optional_reference(value: JsonValue) -> LearningReference | None:
    if value is None:
        return None
    return _reference(_object(value, "reference"))


def _target(payload: Mapping[str, JsonValue]) -> LearningTarget:
    return LearningTarget(
        resource_type=_enum(LearningTargetType, payload, "resource_type"),
        resource_id=_required_string(payload, "resource_id"),
        revision=_required_positive_int(payload, "revision"),
    )


def _optional_target(value: JsonValue) -> LearningTarget | None:
    if value is None:
        return None
    return _target(_object(value, "target"))


def _gate_plan(payload: Mapping[str, JsonValue]) -> LearningGatePlan:
    return LearningGatePlan(
        policy_id=_required_string(payload, "policy_id"),
        policy_version=_required_positive_int(payload, "policy_version"),
        require_evaluation=_optional_bool(
            payload.get("require_evaluation"),
            "require_evaluation",
            True,
        ),
        require_verification=_optional_bool(
            payload.get("require_verification"),
            "require_verification",
            False,
        ),
        require_regression_free=_optional_bool(
            payload.get("require_regression_free"),
            "require_regression_free",
            True,
        ),
        approval_required_risks=tuple(
            _enum_value(RiskClassification, item, "approval_required_risks")
            for item in _list(payload.get("approval_required_risks"), "approval_required_risks")
        )
        or (RiskClassification.HIGH, RiskClassification.CRITICAL),
        automatic_promotion_allowed=_optional_bool(
            payload.get("automatic_promotion_allowed"),
            "automatic_promotion_allowed",
            False,
        ),
        evaluation_suite_refs=_string_tuple(
            payload.get("evaluation_suite_refs"),
            "evaluation_suite_refs",
        ),
        verification_policy_refs=_string_tuple(
            payload.get("verification_policy_refs"),
            "verification_policy_refs",
        ),
    )


def _list(value: JsonValue, field_name: str) -> list[JsonValue]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be an array")
    return value


def _object(value: JsonValue, field_name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must contain objects")
    return value


def _enum[EnumT: StrEnum](
    enum_type: type[EnumT], payload: Mapping[str, JsonValue], field_name: str
) -> EnumT:
    return _enum_value(enum_type, payload.get(field_name), field_name)


def _enum_value[EnumT: StrEnum](enum_type: type[EnumT], value: JsonValue, field_name: str) -> EnumT:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field_name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"invalid {field_name}: {value}",
        ) from exc
