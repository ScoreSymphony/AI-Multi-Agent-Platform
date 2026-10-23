"""Persistence seams for personal frontend preferences."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Protocol, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import FrontendPreference


class FrontendPreferenceRepository(Protocol):
    def get(self, principal_ref: str) -> FrontendPreference | None: ...

    def save(
        self,
        preference: FrontendPreference,
        *,
        expected_revision: int,
    ) -> FrontendPreference: ...

    def delete(self, principal_ref: str, *, expected_revision: int) -> None: ...


class InMemoryFrontendPreferenceRepository:
    def __init__(self) -> None:
        self._items: dict[str, FrontendPreference] = {}

    def get(self, principal_ref: str) -> FrontendPreference | None:
        return self._items.get(principal_ref)

    def save(
        self,
        preference: FrontendPreference,
        *,
        expected_revision: int,
    ) -> FrontendPreference:
        current = self._items.get(preference.principal_ref)
        current_revision = 0 if current is None else current.revision
        if current_revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "frontend preference revision changed",
                details={"current_revision": current_revision},
            )
        self._items[preference.principal_ref] = preference
        return preference

    def delete(self, principal_ref: str, *, expected_revision: int) -> None:
        current = self._items.get(principal_ref)
        current_revision = 0 if current is None else current.revision
        if current_revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "frontend preference revision changed",
                details={"current_revision": current_revision},
            )
        self._items.pop(principal_ref, None)


class SqliteFrontendPreferenceRepository:
    """Restart-safe personal UI preference store."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS frontend_preferences (
                        principal_ref TEXT PRIMARY KEY,
                        schema_version INTEGER NOT NULL,
                        revision INTEGER NOT NULL,
                        updated_at TEXT NOT NULL,
                        customization_json TEXT NOT NULL
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to initialize frontend preference storage",
            ) from exc

    def get(self, principal_ref: str) -> FrontendPreference | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT principal_ref, schema_version, revision, updated_at, customization_json
                    FROM frontend_preferences
                    WHERE principal_ref = ?
                    """,
                    (principal_ref,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to read frontend preference",
            ) from exc
        if row is None:
            return None
        return _decode(row)

    def save(
        self,
        preference: FrontendPreference,
        *,
        expected_revision: int,
    ) -> FrontendPreference:
        customization = preference.customization
        if customization is None:
            raise ValueError("persisted frontend preference requires customization")
        encoded = json.dumps(
            customization,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        try:
            with self._connect() as connection:
                current = connection.execute(
                    "SELECT revision FROM frontend_preferences WHERE principal_ref = ?",
                    (preference.principal_ref,),
                ).fetchone()
                current_revision = 0 if current is None else int(current["revision"])
                if current_revision != expected_revision:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "frontend preference revision changed",
                        details={"current_revision": current_revision},
                    )
                connection.execute(
                    """
                    INSERT INTO frontend_preferences(
                        principal_ref, schema_version, revision, updated_at, customization_json
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(principal_ref) DO UPDATE SET
                        schema_version = excluded.schema_version,
                        revision = excluded.revision,
                        updated_at = excluded.updated_at,
                        customization_json = excluded.customization_json
                    """,
                    (
                        preference.principal_ref,
                        preference.schema_version,
                        preference.revision,
                        preference.updated_at.isoformat() if preference.updated_at else "",
                        encoded,
                    ),
                )
        except ContractError:
            raise
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to persist frontend preference",
            ) from exc
        return preference

    def delete(self, principal_ref: str, *, expected_revision: int) -> None:
        try:
            with self._connect() as connection:
                current = connection.execute(
                    "SELECT revision FROM frontend_preferences WHERE principal_ref = ?",
                    (principal_ref,),
                ).fetchone()
                current_revision = 0 if current is None else int(current["revision"])
                if current_revision != expected_revision:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "frontend preference revision changed",
                        details={"current_revision": current_revision},
                    )
                connection.execute(
                    "DELETE FROM frontend_preferences WHERE principal_ref = ?",
                    (principal_ref,),
                )
        except ContractError:
            raise
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to reset frontend preference",
            ) from exc


def _decode(row: sqlite3.Row) -> FrontendPreference:
    import datetime as _datetime

    try:
        decoded = json.loads(cast(str, row["customization_json"]))
    except (TypeError, json.JSONDecodeError) as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "stored frontend preference is invalid JSON",
        ) from exc
    if not isinstance(decoded, dict):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "stored frontend preference must be an object",
        )
    return FrontendPreference(
        principal_ref=cast(str, row["principal_ref"]),
        schema_version=int(row["schema_version"]),
        revision=int(row["revision"]),
        updated_at=_datetime.datetime.fromisoformat(cast(str, row["updated_at"])),
        customization=cast(dict[str, JsonValue], decoded),
    )
