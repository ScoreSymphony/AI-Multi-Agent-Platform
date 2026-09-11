"""Local FileProvider reference implementation."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    HealthStatus,
    JsonValue,
    OperationContext,
    ProviderDescriptor,
    StoredObject,
)
from ai_multi_agent_platform.domain import validate_id

from .contracts import FileProvider
from .models import DataAccessContext, FileRecord, FileState, OrphanReport, new_file_id
from .reference_support import (
    SqliteReferenceStore as _SqliteMixin,
    compat_context as _compat_context,
    forbidden as _forbidden,
    json_dict as _json_dict,
    json_dump as _json_dump,
    not_found as _not_found,
    parse_time as _parse_time,
)


class LocalFileProvider(_SqliteMixin, FileProvider):
    """Filesystem bytes with SQLite metadata and canonical file IDs."""

    def __init__(self, root: str | Path, db_path: str | Path) -> None:
        self._root = Path(root)
        self._db_path = Path(db_path)
        self._root.mkdir(parents=True, exist_ok=True)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        capability = Capability(
            name="local-files",
            kind=CapabilityKind.FILE,
            supported_operations=(
                "create",
                "read",
                "stream",
                "metadata",
                "list",
                "delete",
                "checksum",
                "artifact_link",
                "orphan_detection",
            ),
            features=("sha256", "tombstones", "project_scope"),
        )
        self._descriptor = ProviderDescriptor(
            provider_id="local-file-reference",
            provider_type="file",
            supported_operations=capability.supported_operations,
            capabilities=(capability,),
            health=HealthStatus.HEALTHY,
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS data_files (
                    file_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    owner_ref TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    content_type TEXT,
                    artifact_ids_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )

    async def create_file(
        self,
        data: bytes,
        context: DataAccessContext,
        *,
        file_id: str | None = None,
        content_type: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> FileRecord:
        canonical_id = file_id or new_file_id()
        validate_id(canonical_id, "file")
        digest = hashlib.sha256(data).hexdigest()
        now = datetime.now(UTC)
        pending = FileRecord(
            file_id=canonical_id,
            project_id=context.project_id,
            owner_ref=context.actor_ref,
            created_by=context.actor_ref,
            created_at=now,
            size_bytes=len(data),
            sha256=digest,
            state=FileState.PENDING,
            content_type=content_type,
            metadata=metadata or {},
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO data_files (
                        file_id, project_id, owner_ref, created_by, created_at, size_bytes,
                        sha256, state, content_type, artifact_ids_json, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pending.file_id,
                        pending.project_id,
                        pending.owner_ref,
                        pending.created_by,
                        pending.created_at.isoformat(),
                        pending.size_bytes,
                        pending.sha256,
                        pending.state.value,
                        pending.content_type,
                        "[]",
                        _json_dump(pending.metadata),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ContractError(ErrorCode.CONFLICT, f"file already exists: {canonical_id}") from exc
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to persist file metadata") from exc

        final_path = self._root / canonical_id
        temp_path = self._root / f".{canonical_id}.pending"
        try:
            temp_path.write_bytes(data)
            os.replace(temp_path, final_path)
            with self._connect() as connection:
                connection.execute(
                    "UPDATE data_files SET state = ? WHERE file_id = ?",
                    (FileState.READY.value, canonical_id),
                )
        except OSError as exc:
            temp_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "UPDATE data_files SET state = ? WHERE file_id = ?",
                    (FileState.TOMBSTONED.value, canonical_id),
                )
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to persist file bytes") from exc
        except sqlite3.Error as exc:
            final_path.unlink(missing_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "UPDATE data_files SET state = ? WHERE file_id = ?",
                    (FileState.TOMBSTONED.value, canonical_id),
                )
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to finalize file metadata"
            ) from exc
        return replace(pending, state=FileState.READY)

    async def write(
        self,
        object_ref: str,
        data: bytes,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject:
        record = await self.create_file(
            data,
            _compat_context(context),
            file_id=object_ref,
            metadata=metadata,
        )
        return StoredObject(
            object_ref=record.file_id,
            metadata={"sha256": record.sha256, "size_bytes": record.size_bytes},
        )

    async def get_file(self, file_id: str, context: DataAccessContext) -> FileRecord:
        validate_id(file_id, "file")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM data_files WHERE file_id = ?",
                    (file_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to read file metadata") from exc
        if row is None:
            raise _not_found("file", file_id)
        record = self._file_from_row(row)
        if record.state is FileState.TOMBSTONED:
            raise _not_found("file", file_id)
        self._check_project(record.project_id, context)
        return record

    async def list_files(self, context: DataAccessContext) -> tuple[FileRecord, ...]:
        try:
            with self._connect() as connection:
                if context.project_id is None:
                    rows = connection.execute(
                        "SELECT * FROM data_files WHERE project_id IS NULL "
                        "AND state != ? ORDER BY created_at",
                        (FileState.TOMBSTONED.value,),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT * FROM data_files WHERE project_id = ? "
                        "AND state != ? ORDER BY created_at",
                        (context.project_id, FileState.TOMBSTONED.value),
                    ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to list files") from exc
        return tuple(self._file_from_row(row) for row in rows)

    async def read(self, object_ref: str, context: OperationContext) -> bytes:
        access = _compat_context(context)
        record = await self.get_file(object_ref, access)
        path = self._root / record.file_id
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise _not_found("file object", record.file_id) from exc
        except OSError as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to read file bytes") from exc
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                f"checksum mismatch for file: {record.file_id}",
            )
        return data

    async def stream_file(
        self,
        file_id: str,
        context: DataAccessContext,
        *,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        if chunk_size <= 0:
            raise ContractError(ErrorCode.INVALID_REQUEST, "chunk_size must be greater than zero")
        data = await self.read(file_id, context.operation)
        for offset in range(0, len(data), chunk_size):
            yield data[offset : offset + chunk_size]

    async def delete_file(self, file_id: str, context: DataAccessContext) -> FileRecord:
        record = await self.get_file(file_id, context)
        try:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE data_files SET state = ? WHERE file_id = ?",
                    (FileState.TOMBSTONED.value, file_id),
                )
            (self._root / file_id).unlink(missing_ok=True)
        except (sqlite3.Error, OSError) as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to tombstone file") from exc
        return replace(record, state=FileState.TOMBSTONED)

    async def verify_checksum(self, file_id: str, context: DataAccessContext) -> bool:
        record = await self.get_file(file_id, context)
        path = self._root / record.file_id
        try:
            if not path.exists():
                return False
            return hashlib.sha256(path.read_bytes()).hexdigest() == record.sha256
        except OSError as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to verify file checksum") from exc

    async def link_artifact(
        self,
        file_id: str,
        artifact_id: str,
        context: DataAccessContext,
    ) -> FileRecord:
        validate_id(artifact_id, "artifact")
        record = await self.get_file(file_id, context)
        artifact_ids = record.artifact_ids
        if artifact_id not in artifact_ids:
            artifact_ids = (*artifact_ids, artifact_id)
            try:
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE data_files SET artifact_ids_json = ? WHERE file_id = ?",
                        (_json_dump(list(artifact_ids)), file_id),
                    )
            except sqlite3.Error as exc:
                raise ContractError(ErrorCode.BACKEND_ERROR, "failed to link artifact") from exc
        return replace(record, artifact_ids=artifact_ids)

    async def detect_orphans(self, context: DataAccessContext) -> OrphanReport:
        records = await self.list_files(context)
        known = {record.file_id for record in records}
        missing = tuple(sorted(file_id for file_id in known if not (self._root / file_id).exists()))
        unreferenced = tuple(
            sorted(
                path.name
                for path in self._root.iterdir()
                if path.is_file() and path.name.startswith("file_") and path.name not in known
            )
        )
        return OrphanReport(missing_objects=missing, unreferenced_objects=unreferenced)

    @staticmethod
    def _check_project(project_id: str | None, context: DataAccessContext) -> None:
        if project_id != context.project_id:
            raise _forbidden("file belongs to a different project/workspace scope")

    @staticmethod
    def _file_from_row(row: sqlite3.Row) -> FileRecord:
        raw_artifacts = json.loads(cast(str, row["artifact_ids_json"]))
        if not isinstance(raw_artifacts, list) or not all(
            isinstance(item, str) for item in raw_artifacts
        ):
            raise ContractError(ErrorCode.CONTRACT_VIOLATION, "stored artifact IDs are invalid")
        return FileRecord(
            file_id=cast(str, row["file_id"]),
            project_id=cast(str | None, row["project_id"]),
            owner_ref=cast(str, row["owner_ref"]),
            created_by=cast(str, row["created_by"]),
            created_at=_parse_time(cast(str, row["created_at"])),
            size_bytes=cast(int, row["size_bytes"]),
            sha256=cast(str, row["sha256"]),
            state=FileState(cast(str, row["state"])),
            content_type=cast(str | None, row["content_type"]),
            artifact_ids=tuple(cast(list[str], raw_artifacts)),
            metadata=_json_dict(cast(str, row["metadata_json"])),
        )
