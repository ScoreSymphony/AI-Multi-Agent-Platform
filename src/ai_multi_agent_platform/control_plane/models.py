"""Stable value types for the versioned northbound Control Plane."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id, validate_id

API_VERSION = "v1"
SUPPORTED_API_VERSIONS = (API_VERSION,)
CURRENT_COLLECTIONS = (
    "projects",
    "workspaces",
    "tasks",
    "plans",
    "steps",
    "runs",
    "artifacts",
    "results",
)

OwnerType = Literal["user", "organization", "team", "service"]

_ERROR_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.UNSUPPORTED_CAPABILITY: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.UNAVAILABLE: 503,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.CANCELLED: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.RESOURCE_EXHAUSTED: 429,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.TRANSIENT_FAILURE: 503,
    ErrorCode.PERMANENT_FAILURE: 422,
    ErrorCode.CONTRACT_VIOLATION: 500,
    ErrorCode.BACKEND_ERROR: 502,
}


@dataclass(frozen=True, slots=True)
class ActorContext:
    """Transport-neutral actor context; authentication itself belongs to #36."""

    principal_ref: str = "local:anonymous"
    owner_type: OwnerType | None = None
    owner_id: str | None = None

    def __post_init__(self) -> None:
        if not self.principal_ref.strip():
            raise ValueError("principal_ref must not be blank")
        if (self.owner_type is None) != (self.owner_id is None):
            raise ValueError("owner_type and owner_id must both be set or both be omitted")


@dataclass(frozen=True, slots=True)
class RequestContext:
    request_id: str
    correlation_id: str
    actor: ActorContext = ActorContext()
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("request_id must not be blank")
        if not self.correlation_id.strip():
            raise ValueError("correlation_id must not be blank")
        if self.idempotency_key is not None and not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")


@dataclass(frozen=True, slots=True)
class PageQuery:
    limit: int = 50
    cursor: str | None = None
    sort: str = "id"
    direction: Literal["asc", "desc"] = "asc"
    search: str | None = None
    filters: dict[str, str] | None = None
    fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if not self.sort.strip():
            raise ValueError("sort must not be blank")


@dataclass(frozen=True, slots=True)
class WorkspaceIdentity:
    """Minimal canonical identity only; #37 owns workspace lifecycle/materialization."""

    project_id: str
    owner_type: OwnerType
    owner_id: str
    id: str = ""
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        workspace_id = self.id or new_id("workspace")
        object.__setattr__(self, "id", workspace_id)
        object.__setattr__(self, "created_at", self.created_at or datetime.now(UTC))
        validate_id(workspace_id, "workspace")
        validate_id(self.project_id, "project")
        if not self.owner_id.strip():
            raise ValueError("owner_id must not be blank")


@dataclass(frozen=True, slots=True)
class APIError:
    code: str
    message: str
    request_id: str
    correlation_id: str
    retryable: bool = False
    details: dict[str, JsonValue] | None = None
    diagnostics: dict[str, dict[str, JsonValue]] | None = None

    def to_json(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "code": self.code,
            "message": self.message,
            "request_id": self.request_id,
            "correlation_id": self.correlation_id,
            "retryable": self.retryable,
        }
        if self.details:
            payload["details"] = self.details
        if self.diagnostics:
            diagnostics: dict[str, JsonValue] = {
                namespace: dict(values) for namespace, values in self.diagnostics.items()
            }
            payload["diagnostics"] = diagnostics
        return payload


class APIException(Exception):
    def __init__(
        self,
        *,
        status: int,
        code: str,
        message: str,
        retryable: bool = False,
        details: dict[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


def api_exception_from_contract(error: ContractError) -> APIException:
    """Translate canonical service errors without leaking backend exception objects."""

    return APIException(
        status=_ERROR_STATUS.get(error.code, 500),
        code=error.code.value,
        message=error.message,
        retryable=error.retryable,
        details=dict(error.details),
    )


def json_value(value: Any) -> JsonValue:
    """Detach canonical dataclasses/mappings into JSON-safe values."""

    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return json_value(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [json_value(item) for item in value]
    raise TypeError(f"unsupported API serialization type: {type(value).__name__}")


def json_object(value: Any) -> dict[str, JsonValue]:
    converted = json_value(value)
    if not isinstance(converted, dict):
        raise TypeError("expected JSON object")
    return converted


def paginate(
    items: Sequence[dict[str, JsonValue]],
    query: PageQuery,
) -> dict[str, JsonValue]:
    filtered = list(items)
    for name, expected in (query.filters or {}).items():
        filtered = [
            item for item in filtered if name in item and _filter_value(item[name]) == expected
        ]

    if query.search:
        needle = query.search.casefold()
        filtered = [
            item
            for item in filtered
            if needle in json.dumps(item, sort_keys=True, default=str).casefold()
        ]

    filtered.sort(
        key=lambda item: _filter_value(item.get(query.sort)),
        reverse=query.direction == "desc",
    )
    offset = _decode_cursor(query.cursor) if query.cursor else 0
    window = filtered[offset : offset + query.limit]
    next_offset = offset + len(window)
    next_cursor = _encode_cursor(next_offset) if next_offset < len(filtered) else None
    selected = [_select_fields(item, query.fields) for item in window]
    selected_json: list[JsonValue] = [dict(item) for item in selected]
    return {
        "items": selected_json,
        "next_cursor": next_cursor,
        "total": len(filtered),
        "limit": query.limit,
    }


def validation_error(field: str, message: str) -> APIException:
    return APIException(
        status=400,
        code="invalid_request",
        message=message,
        details={"field": field},
    )


def _select_fields(
    item: dict[str, JsonValue],
    selected_fields: tuple[str, ...],
) -> dict[str, JsonValue]:
    if not selected_fields:
        return dict(item)
    wanted = {"id", "type"} | set(selected_fields)
    return {name: value for name, value in item.items() if name in wanted}


def _filter_value(value: JsonValue | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> int:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode((cursor + padding).encode("ascii")).decode("ascii")
        offset = int(decoded)
    except (ValueError, UnicodeDecodeError) as exc:
        raise APIException(status=400, code="invalid_cursor", message="cursor is invalid") from exc
    if offset < 0:
        raise APIException(status=400, code="invalid_cursor", message="cursor is invalid")
    return offset
