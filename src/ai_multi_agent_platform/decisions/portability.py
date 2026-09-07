"""Safe Decision Record import/export without resource activation (#598)."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import DecisionRecord, DecisionStatus
from .repository import (
    _record_from_json,
    _record_to_json,
    _reference_from_json,
    _reference_to_json,
)
from .service import DecisionService

DECISION_BUNDLE_SCHEMA_VERSION = "1.0"


def export_decision_bundle(decisions: DecisionService) -> dict[str, JsonValue]:
    """Export canonical history and references; never export executable owner actions."""

    records: list[JsonValue] = []
    for view in decisions.list_views():
        records.append(
            {
                "record": _record_to_json(view.record),
                "status": view.status.value,
                "superseded_by": view.superseded_by,
                "withdrawal_reason": view.withdrawal_reason,
                "downstream_refs": [_reference_to_json(item) for item in view.downstream_refs],
            }
        )
    return {
        "schema_version": DECISION_BUNDLE_SCHEMA_VERSION,
        "kind": "decision-record-bundle",
        "records": records,
        "activation_semantics": "none",
    }


def import_decision_bundle(
    decisions: DecisionService,
    bundle: dict[str, JsonValue],
    *,
    actor_ref: str,
) -> tuple[str, ...]:
    """Import historical state only.

    No provider/plugin/policy/resource activation is callable here.
    """

    if bundle.get("schema_version") != DECISION_BUNDLE_SCHEMA_VERSION:
        raise ContractError(ErrorCode.INVALID_REQUEST, "unsupported DecisionRecord bundle version")
    if bundle.get("kind") != "decision-record-bundle":
        raise ContractError(ErrorCode.INVALID_REQUEST, "invalid DecisionRecord bundle kind")
    raw_records = bundle.get("records")
    if not isinstance(raw_records, list):
        raise ContractError(
            ErrorCode.INVALID_REQUEST, "DecisionRecord bundle records must be an array"
        )

    pending: dict[str, tuple[DecisionRecord, dict[str, JsonValue]]] = {}
    for raw in raw_records:
        item = _object(raw)
        record = _record_from_json(_object(item.get("record")))
        pending[record.id] = (record, item)

    imported: list[str] = []
    while pending:
        progressed = False
        for record_id, (record, item) in tuple(pending.items()):
            del item
            predecessor = record.supersedes
            if predecessor is not None and predecessor in pending:
                continue
            try:
                existing = decisions.repository.get(record.id)
            except ContractError as exc:
                if exc.code is not ErrorCode.NOT_FOUND:
                    raise
                existing = None
            if existing is not None:
                if existing.content_digest != record.content_digest:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "imported DecisionRecord ID is bound to different immutable content",
                    )
            elif predecessor is None:
                decisions.create(record)
            else:
                decisions.supersede(predecessor, record)
            imported.append(record.id)
            del pending[record_id]
            progressed = True
        if not progressed:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "DecisionRecord bundle has missing/cyclic supersession dependencies",
            )

    for raw in raw_records:
        item = _object(raw)
        record = _record_from_json(_object(item.get("record")))
        downstream = item.get("downstream_refs", [])
        if not isinstance(downstream, list):
            raise ContractError(ErrorCode.INVALID_REQUEST, "downstream_refs must be an array")
        for raw_reference in downstream:
            decisions.repository.add_downstream_ref(
                record.id,
                _reference_from_json(_object(raw_reference)),
            )
        if item.get("status") == DecisionStatus.WITHDRAWN.value:
            reason = item.get("withdrawal_reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "withdrawn imported DecisionRecord requires withdrawal_reason",
                )
            if decisions.view(record.id).status is DecisionStatus.CURRENT:
                decisions.repository.withdraw(record.id, actor_ref=actor_ref, reason=reason)

    return tuple(imported)


def _object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_REQUEST, "DecisionRecord bundle item must be an object"
        )
    return value
