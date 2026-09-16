"""Restart-durable SQLite persistence for canonical Application state."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.domain import validate_id

from .definition import Application
from .models import ApplicationInstance
from .repository import ApplicationRepository
from .serialization import (
    decode_application,
    decode_application_instance,
    encode_application,
    encode_application_instance,
)

_SCHEMA_VERSION = 1


class SqliteApplicationRepository(ApplicationRepository):
    """Restart-durable single-node repository for Applications and instances.

    Only canonical metadata is persisted. Secret bindings contain ``SecretReference``
    objects encoded by the canonical security serializer; resolved secret material never
    crosses this repository boundary.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @property
    def schema_version(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > _SCHEMA_VERSION:
                raise RuntimeError(
                    "application persistence schema is newer than this runtime supports: "
                    f"{current} > {_SCHEMA_VERSION}"
                )
            if current < 1:
                self._migrate_to_v1(connection)
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    @staticmethod
    def _migrate_to_v1(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS applications (
                application_id TEXT NOT NULL,
                version TEXT NOT NULL,
                runtime_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (application_id, version)
            );

            CREATE TABLE IF NOT EXISTS application_instances (
                instance_id TEXT PRIMARY KEY,
                application_id TEXT NOT NULL,
                application_version TEXT NOT NULL,
                runtime_id TEXT NOT NULL,
                revision INTEGER NOT NULL CHECK (revision >= 1),
                payload_json TEXT NOT NULL,
                FOREIGN KEY (application_id, application_version)
                    REFERENCES applications(application_id, version)
                    ON DELETE RESTRICT
            );

            CREATE INDEX IF NOT EXISTS idx_application_instances_application
                ON application_instances(application_id, application_version, instance_id);
            """
        )

    def save_application(self, application: Application) -> Application:
        payload = encode_application(application)
        key = (application.application_id, application.version)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT payload_json
                FROM applications
                WHERE application_id = ? AND version = ?
                """,
                key,
            ).fetchone()
            if row is not None:
                current = decode_application(str(row["payload_json"]))
                if not _same_application_definition(current, application):
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "application definition is immutable for an existing application/version",
                        details={
                            "application_id": application.application_id,
                            "version": application.version,
                        },
                    )
                return current
            connection.execute(
                """
                INSERT INTO applications (application_id, version, runtime_id, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    application.application_id,
                    application.version,
                    application.runtime_id,
                    payload,
                ),
            )
        return application

    def get_application(self, application_id: str, version: str) -> Application:
        validate_id(application_id, "application")
        if not version.strip():
            raise ValueError("version must not be blank")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM applications
                WHERE application_id = ? AND version = ?
                """,
                (application_id, version),
            ).fetchone()
        if row is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"application not found: {application_id!r} {version!r}",
            )
        return decode_application(str(row["payload_json"]))

    def list_applications(self) -> tuple[Application, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM applications
                ORDER BY application_id, version
                """
            ).fetchall()
        return tuple(decode_application(str(row["payload_json"])) for row in rows)

    def save_instance(self, instance: ApplicationInstance) -> ApplicationInstance:
        payload = encode_application_instance(instance)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT revision, payload_json
                FROM application_instances
                WHERE instance_id = ?
                """,
                (instance.instance_id,),
            ).fetchone()
            if row is not None:
                current_revision = int(row["revision"])
                if instance.revision < current_revision:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "application instance revision must not move backwards",
                        details={
                            "instance_id": instance.instance_id,
                            "current_revision": current_revision,
                            "proposed_revision": instance.revision,
                        },
                    )
                if instance.revision == current_revision:
                    current = decode_application_instance(str(row["payload_json"]))
                    if instance != current:
                        raise ContractError(
                            ErrorCode.CONFLICT,
                            "application instance updates require a new revision",
                            details={
                                "instance_id": instance.instance_id,
                                "revision": instance.revision,
                            },
                        )
                    return current
            try:
                connection.execute(
                    """
                    INSERT INTO application_instances (
                        instance_id,
                        application_id,
                        application_version,
                        runtime_id,
                        revision,
                        payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(instance_id) DO UPDATE SET
                        application_id = excluded.application_id,
                        application_version = excluded.application_version,
                        runtime_id = excluded.runtime_id,
                        revision = excluded.revision,
                        payload_json = excluded.payload_json
                    """,
                    (
                        instance.instance_id,
                        instance.application_id,
                        instance.application_version,
                        instance.runtime_id,
                        instance.revision,
                        payload,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "application instance references an application definition "
                    "that is not persisted",
                    details={
                        "instance_id": instance.instance_id,
                        "application_id": instance.application_id,
                        "application_version": instance.application_version,
                    },
                ) from exc
        return instance

    def get_instance(self, instance_id: str) -> ApplicationInstance:
        validate_id(instance_id, "application_instance")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM application_instances WHERE instance_id = ?",
                (instance_id,),
            ).fetchone()
        if row is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"application instance not found: {instance_id}",
            )
        return decode_application_instance(str(row["payload_json"]))

    def list_instances(
        self,
        *,
        application_id: str | None = None,
    ) -> tuple[ApplicationInstance, ...]:
        if application_id is not None:
            validate_id(application_id, "application")
        with self._connect() as connection:
            if application_id is None:
                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM application_instances
                    ORDER BY instance_id
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM application_instances
                    WHERE application_id = ?
                    ORDER BY instance_id
                    """,
                    (application_id,),
                ).fetchall()
        return tuple(decode_application_instance(str(row["payload_json"])) for row in rows)


def _same_application_definition(left: Application, right: Application) -> bool:
    return (
        left.manifest == right.manifest
        and left.runtime_id == right.runtime_id
        and left.source_ref == right.source_ref
        and dict(left.provenance) == dict(right.provenance)
    )
