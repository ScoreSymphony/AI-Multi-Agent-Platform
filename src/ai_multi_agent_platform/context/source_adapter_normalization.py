"""Shared normalization primitives for operational Context source adapters."""

from __future__ import annotations

import hashlib
import json

from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    ErrorCode,
    OperationContext,
)
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import ContextDataClassification
from .resolver import ContextSourceRequest


def canonical_json(value: JsonValue) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_digest(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def normalize_classification(
    value: DataClassification | str | None,
) -> ContextDataClassification:
    if value is None:
        return ContextDataClassification.INTERNAL
    try:
        normalized = value if isinstance(value, DataClassification) else DataClassification(value)
    except ValueError:
        return ContextDataClassification.RESTRICTED
    if normalized is DataClassification.PUBLIC:
        return ContextDataClassification.PUBLIC
    if normalized is DataClassification.INTERNAL:
        return ContextDataClassification.INTERNAL
    if normalized in {DataClassification.CONFIDENTIAL, DataClassification.PRIVATE}:
        return ContextDataClassification.CONFIDENTIAL
    if normalized is DataClassification.RESTRICTED:
        return ContextDataClassification.RESTRICTED
    return ContextDataClassification.SECRET_REFERENCE


def operational_request(request: ContextSourceRequest) -> tuple[OperationContext, str]:
    operation = getattr(request, "operation", None)
    actor_ref = getattr(request, "actor_ref", None)
    if not isinstance(operation, OperationContext):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "operational Context source adapter requires OperationContext",
        )
    if not isinstance(actor_ref, str) or not actor_ref.strip():
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "operational Context source adapter requires actor_ref",
        )
    return operation, actor_ref
