"""Shared persistence primitives for local reference data providers.

This module deliberately contains only cross-provider serialization, access-context and
SQLite connection mechanics. Provider-specific schema, scope and lifecycle behavior lives
next to the File, Memory or Knowledge implementation that owns it.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext

from .models import DataAccessContext


def json_dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def json_value(value: str) -> JsonValue:
    return cast(JsonValue, json.loads(value))


def json_dict(value: str) -> dict[str, JsonValue]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "stored metadata is not an object")
    return cast(dict[str, JsonValue], loaded)


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "stored timestamp is not timezone-aware")
    return parsed


def optional_time(value: str | None) -> datetime | None:
    return None if value is None else parse_time(value)


def actor_ref(operation: OperationContext) -> str:
    if operation.owner_type is not None and operation.owner_id is not None:
        return f"{operation.owner_type}:{operation.owner_id}"
    return "service:unspecified"


def compat_context(operation: OperationContext) -> DataAccessContext:
    return DataAccessContext(operation=operation, actor_ref=actor_ref(operation))


def forbidden(message: str) -> ContractError:
    return ContractError(ErrorCode.FORBIDDEN, message)


def not_found(kind: str, ref: str) -> ContractError:
    return ContractError(ErrorCode.NOT_FOUND, f"{kind} not found: {ref}")


class SqliteReferenceStore:
    """Narrow connection primitive shared by the local SQLite reference providers."""

    _db_path: Path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
