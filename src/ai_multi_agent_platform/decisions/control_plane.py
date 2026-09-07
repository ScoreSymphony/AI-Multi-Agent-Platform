"""Control Plane resources and commands for canonical Decision Records (#598)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .models import (
    DecisionAlternative,
    DecisionAlternativeStatus,
    DecisionOutcome,
    DecisionRecord,
    DecisionRecordView,
    DecisionReference,
    alternative_to_json,
    reference_to_json,
)
from .service import DecisionService

DECISION_COLLECTION = "decision-records"
DECISION_COMMANDS = (
    "decision-record.create",
    "decision-record.supersede",
    "decision-record.withdraw",
    "decision-record.link-provenance",
)

DecisionVisibility = Callable[[RequestContext, DecisionRecordView], Awaitable[bool]]


class DecisionRecordResourceService:
    """Read projection with optional object-scoped visibility filtering."""

    def __init__(
        self,
        decisions: DecisionService,
        *,
        visibility: DecisionVisibility | None = None,
    ) -> None:
        self._decisions = decisions
        self._visibility = visibility

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for view in self._decisions.list_views():
            if self._visibility is None or await self._visibility(context, view):
                resources.append(decision_view_resource(view))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        view = self._decisions.view(resource_id)
        if self._visibility is not None and not await self._visibility(context, view):
            raise ContractError(ErrorCode.NOT_FOUND, "DecisionRecord was not found")
        return decision_view_resource(view)


class DecisionRecordCommandHandler:
    """Mutation handler; authorization remains owned by the Control Plane/#15 boundary."""

    def __init__(self, decisions: DecisionService, command: str) -> None:
        if command not in DECISION_COMMANDS:
            raise ValueError(f"unsupported DecisionRecord command: {command}")
        self._decisions = decisions
        self._command = command

    async def __call__(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if self._command == "decision-record.create":
            if resource_ref != DECISION_COLLECTION:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    f"create resource_ref must be {DECISION_COLLECTION!r}",
                )
            record = _record_from_payload(payload, actor_ref=context.actor.principal_ref)
            return decision_view_resource(self._decisions.create(record))

        if self._command == "decision-record.supersede":
            previous = self._decisions.view(resource_ref)
            record = _record_from_payload(
                payload,
                actor_ref=context.actor.principal_ref,
                supersedes=resource_ref,
                revision=previous.record.revision + 1,
            )
            return decision_view_resource(self._decisions.supersede(resource_ref, record))

        if self._command == "decision-record.withdraw":
            reason = _required_string(payload, "reason")
            return decision_view_resource(
                self._decisions.withdraw(
                    resource_ref,
                    actor_ref=context.actor.principal_ref,
                    reason=reason,
                )
            )

        reference = _reference(_required_object(payload, "reference"))
        return decision_view_resource(
            self._decisions.link_downstream_provenance(resource_ref, reference)
        )


def decision_record_resource_services(
    decisions: DecisionService,
    *,
    visibility: DecisionVisibility | None = None,
) -> dict[str, DecisionRecordResourceService]:
    return {
        DECISION_COLLECTION: DecisionRecordResourceService(
            decisions,
            visibility=visibility,
        )
    }


def decision_record_command_handlers(
    decisions: DecisionService,
) -> dict[str, DecisionRecordCommandHandler]:
    return {
        command: DecisionRecordCommandHandler(decisions, command) for command in DECISION_COMMANDS
    }


def decision_view_resource(view: DecisionRecordView) -> dict[str, JsonValue]:
    record = view.record
    return {
        "id": record.id,
        "type": "decision-record",
        "title": record.title,
        "subject": record.subject,
        "category": record.category,
        "scope_type": record.scope_type,
        "scope_id": record.scope_id,
        "subject_ref": _optional_reference_json(record.subject_ref),
        "question": record.question,
        "alternatives": [alternative_to_json(item) for item in record.alternatives],
        "outcome": record.outcome.value,
        "rationale": record.rationale,
        "evidence_refs": [reference_to_json(item) for item in record.evidence_refs],
        "evaluation_refs": [reference_to_json(item) for item in record.evaluation_refs],
        "finding_refs": [reference_to_json(item) for item in record.finding_refs],
        "cost_resource_refs": [reference_to_json(item) for item in record.cost_resource_refs],
        "actor_ref": record.actor_ref,
        "reviewer_refs": list(record.reviewer_refs),
        "approval_ref": _optional_reference_json(record.approval_ref),
        "adr_ref": _optional_reference_json(record.adr_ref),
        "effective_at": record.effective_at.isoformat(),
        "review_at": record.review_at.isoformat() if record.review_at is not None else None,
        "review_condition": record.review_condition,
        "status": view.status.value,
        "supersedes": record.supersedes,
        "superseded_by": view.superseded_by,
        "withdrawn_at": view.withdrawn_at.isoformat() if view.withdrawn_at is not None else None,
        "withdrawal_reason": view.withdrawal_reason,
        "downstream_refs": [reference_to_json(item) for item in view.downstream_refs],
        "revision": record.revision,
        "content_digest": record.content_digest,
        "created_at": record.created_at.isoformat(),
        "schema_version": record.schema_version,
        "revisit_due": _revisit_due(record),
    }


def _record_from_payload(
    payload: dict[str, JsonValue],
    *,
    actor_ref: str,
    supersedes: str | None = None,
    revision: int = 1,
) -> DecisionRecord:
    effective_at = _optional_datetime(payload.get("effective_at")) or datetime.now(UTC)
    return DecisionRecord(
        title=_required_string(payload, "title"),
        subject=_required_string(payload, "subject"),
        category=_required_string(payload, "category"),
        scope_type=_required_string(payload, "scope_type"),
        scope_id=_optional_string(payload.get("scope_id")),
        subject_ref=_optional_reference(payload.get("subject_ref")),
        question=_required_string(payload, "question"),
        alternatives=tuple(
            _alternative(_object(item, "alternative"))
            for item in _required_list(payload, "alternatives")
        ),
        outcome=DecisionOutcome(_required_string(payload, "outcome")),
        rationale=_required_string(payload, "rationale"),
        evidence_refs=_reference_tuple(payload, "evidence_refs"),
        evaluation_refs=_reference_tuple(payload, "evaluation_refs"),
        finding_refs=_reference_tuple(payload, "finding_refs"),
        cost_resource_refs=_reference_tuple(payload, "cost_resource_refs"),
        actor_ref=actor_ref,
        reviewer_refs=tuple(
            _string_item(item, "reviewer_refs") for item in _list(payload, "reviewer_refs")
        ),
        approval_ref=_optional_reference(payload.get("approval_ref")),
        adr_ref=_optional_reference(payload.get("adr_ref")),
        effective_at=effective_at,
        review_at=_optional_datetime(payload.get("review_at")),
        review_condition=_optional_string(payload.get("review_condition")),
        supersedes=supersedes,
        revision=revision,
    )


def _alternative(value: dict[str, JsonValue]) -> DecisionAlternative:
    return DecisionAlternative(
        label=_required_string(value, "label"),
        status=DecisionAlternativeStatus(
            _optional_string(value.get("status")) or DecisionAlternativeStatus.CONSIDERED.value
        ),
        resource_ref=_optional_reference(value.get("resource_ref")),
        evidence_refs=_reference_tuple(value, "evidence_refs"),
        trade_offs=tuple(_string_item(item, "trade_offs") for item in _list(value, "trade_offs")),
        unknowns=tuple(_string_item(item, "unknowns") for item in _list(value, "unknowns")),
    )


def _reference(value: dict[str, JsonValue]) -> DecisionReference:
    revision = value.get("revision")
    if revision is not None and not isinstance(revision, str | int):
        raise ContractError(ErrorCode.INVALID_REQUEST, "reference revision must be string/integer")
    raw_metadata = value.get("metadata", {})
    metadata = _object(raw_metadata, "reference metadata")
    return DecisionReference(
        kind=_required_string(value, "kind"),
        resource_id=_required_string(value, "resource_id"),
        revision=revision,
        digest=_optional_string(value.get("digest")),
        locator=_optional_string(value.get("locator")),
        metadata=metadata,
    )


def _optional_reference(value: JsonValue) -> DecisionReference | None:
    return None if value is None else _reference(_object(value, "reference"))


def _reference_tuple(payload: dict[str, JsonValue], key: str) -> tuple[DecisionReference, ...]:
    return tuple(_reference(_object(item, key)) for item in _list(payload, key))


def _optional_reference_json(reference: DecisionReference | None) -> dict[str, JsonValue] | None:
    return None if reference is None else reference_to_json(reference)


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _optional_string(value: JsonValue) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, "optional decision value must be a string")
    return value


def _required_list(payload: dict[str, JsonValue], key: str) -> list[JsonValue]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-empty array")
    return value


def _list(payload: dict[str, JsonValue], key: str) -> list[JsonValue]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be an array")
    return value


def _required_object(payload: dict[str, JsonValue], key: str) -> dict[str, JsonValue]:
    if key not in payload:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} is required")
    return _object(payload[key], key)


def _object(value: JsonValue, name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be an object")
    return value


def _string_item(value: JsonValue, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must contain non-blank strings")
    return value


def _optional_datetime(value: JsonValue) -> datetime | None:
    text = _optional_string(value)
    if text is None:
        return None
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(ErrorCode.INVALID_REQUEST, "decision datetime must be timezone-aware")
    return parsed


def _revisit_due(record: DecisionRecord) -> bool:
    return record.review_at is not None and record.review_at <= datetime.now(UTC)
