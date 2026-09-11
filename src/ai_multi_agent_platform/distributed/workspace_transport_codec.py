"""Wire and manifest codecs for distributed remote Workspace transport.

This module owns transport serialization, validation and deterministic checksums only.
It deliberately has no transport endpoint, filesystem materialization or control-side
provider behavior so those responsibilities can evolve independently.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security import redact_exception
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    RemoteCleanupAcknowledgement,
    RemoteMaterializationReceipt,
    RemoteMaterializationRequest,
    WorkspaceAccessMode,
    validate_relative_path,
)

from .registry import RegistryError


@dataclass(frozen=True, slots=True)
class _ManifestEntry:
    relative_path: str
    file_id: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        validate_relative_path(self.relative_path)
        if not self.file_id.strip():
            raise ValueError("manifest file_id must not be blank")
        _validate_sha256(self.sha256)
        if self.size_bytes < 0:
            raise ValueError("manifest size_bytes must not be negative")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "relative_path": self.relative_path,
            "file_id": self.file_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_json(cls, value: object) -> _ManifestEntry:
        data = _mapping(value, "workspace manifest entry")
        return cls(
            relative_path=_required_string(data, "relative_path"),
            file_id=_required_string(data, "file_id"),
            sha256=_required_string(data, "sha256"),
            size_bytes=_required_integer(data, "size_bytes", minimum=0),
        )


def _materialization_ref(worker_id: str, request: RemoteMaterializationRequest) -> str:
    digest = hashlib.sha256(
        (
            f"{worker_id}\0{request.workspace_id}\0{request.snapshot_id}\0"
            f"{request.expected_checksum}\0{request.access_mode.value}"
        ).encode()
    ).hexdigest()
    return f"remote-materialization-{digest[:32]}"


def _receipt(
    worker_id: str,
    request: RemoteMaterializationRequest,
    materialization_ref: str,
    *,
    cache_hit: bool,
) -> RemoteMaterializationReceipt:
    return RemoteMaterializationReceipt(
        workspace_id=request.workspace_id,
        snapshot_id=request.snapshot_id,
        expected_checksum=request.expected_checksum,
        observed_checksum=request.expected_checksum,
        access_mode=request.access_mode,
        worker_ref=worker_id,
        materialization_ref=materialization_ref,
        cache_hit=cache_hit,
    )


def _canonical_snapshot_checksum(entries: tuple[_ManifestEntry, ...] | list[_ManifestEntry]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item.relative_path):
        digest.update(entry.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.file_id.encode("ascii"))
        digest.update(b"\0")
        digest.update(entry.sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _tree_content_checksum(files: Mapping[str, tuple[bytes, str]]) -> str:
    digest = hashlib.sha256()
    for relative_path, (_data, sha256) in sorted(files.items()):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _entry_token(entry: _ManifestEntry) -> str:
    return hashlib.sha256(entry.relative_path.encode("utf-8")).hexdigest()


def _encode_request(request: RemoteMaterializationRequest) -> dict[str, JsonValue]:
    return {
        "workspace_id": request.workspace_id,
        "snapshot_id": request.snapshot_id,
        "expected_checksum": request.expected_checksum,
        "access_mode": request.access_mode.value,
        "cache_key": request.cache_key,
    }


def _decode_request(value: object) -> RemoteMaterializationRequest:
    data = _mapping(value, "RemoteMaterializationRequest")
    return RemoteMaterializationRequest(
        workspace_id=_required_string(data, "workspace_id"),
        snapshot_id=_required_string(data, "snapshot_id"),
        expected_checksum=_required_string(data, "expected_checksum"),
        access_mode=WorkspaceAccessMode(_required_string(data, "access_mode")),
        cache_key=_required_string(data, "cache_key"),
    )


def _encode_receipt(receipt: RemoteMaterializationReceipt) -> dict[str, JsonValue]:
    return {
        "workspace_id": receipt.workspace_id,
        "snapshot_id": receipt.snapshot_id,
        "expected_checksum": receipt.expected_checksum,
        "observed_checksum": receipt.observed_checksum,
        "access_mode": receipt.access_mode.value,
        "worker_ref": receipt.worker_ref,
        "materialization_ref": receipt.materialization_ref,
        "cache_hit": receipt.cache_hit,
        "acknowledged_at": receipt.acknowledged_at.isoformat(),
    }


def _decode_receipt(value: object) -> RemoteMaterializationReceipt:
    data = _mapping(value, "RemoteMaterializationReceipt")
    return RemoteMaterializationReceipt(
        workspace_id=_required_string(data, "workspace_id"),
        snapshot_id=_required_string(data, "snapshot_id"),
        expected_checksum=_required_string(data, "expected_checksum"),
        observed_checksum=_required_string(data, "observed_checksum"),
        access_mode=WorkspaceAccessMode(_required_string(data, "access_mode")),
        worker_ref=_required_string(data, "worker_ref"),
        materialization_ref=_required_string(data, "materialization_ref"),
        cache_hit=_boolean(data.get("cache_hit"), "cache_hit"),
        acknowledged_at=_datetime(_required(data, "acknowledged_at")),
    )


def _encode_cleanup(cleanup: RemoteCleanupAcknowledgement) -> dict[str, JsonValue]:
    return {
        "workspace_id": cleanup.workspace_id,
        "snapshot_id": cleanup.snapshot_id,
        "materialization_ref": cleanup.materialization_ref,
        "outcome": cleanup.outcome.value,
        "succeeded": cleanup.succeeded,
        "error_code": cleanup.error_code,
        "acknowledged_at": cleanup.acknowledged_at.isoformat(),
    }


def _decode_cleanup(value: object) -> RemoteCleanupAcknowledgement:
    data = _mapping(value, "RemoteCleanupAcknowledgement")
    return RemoteCleanupAcknowledgement(
        workspace_id=_required_string(data, "workspace_id"),
        snapshot_id=_required_string(data, "snapshot_id"),
        materialization_ref=_required_string(data, "materialization_ref"),
        outcome=MaterializationOutcome(_required_string(data, "outcome")),
        succeeded=_boolean(data.get("succeeded"), "succeeded"),
        error_code=_optional_string(data.get("error_code"), "error_code"),
        acknowledged_at=_datetime(_required(data, "acknowledged_at")),
    )


def _safe_workspace_error(error: Exception) -> str:
    if isinstance(error, (RegistryError, ContractError, ValueError)):
        return redact_exception(error)
    return "remote Workspace operation failed"


def _decode_base64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RegistryError("remote Workspace payload contains invalid base64") from exc


def _required_base64(data: Mapping[str, object], name: str) -> bytes:
    value = _required(data, name)
    if not isinstance(value, str):
        raise RegistryError(f"remote Workspace field {name} must be a base64 string")
    return _decode_base64(value)


def _validate_sha256(value: str) -> None:
    if len(value) != 64:
        raise ValueError("sha256 must contain 64 hexadecimal characters")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError("sha256 must be hexadecimal") from exc


def _validate_opaque(value: str, label: str) -> None:
    if not value.strip() or "/" in value or "\\" in value or value in {".", ".."}:
        raise RegistryError(f"{label} must be an opaque reference")


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise RegistryError(f"{label} must be an object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise RegistryError(f"{label} keys must be strings")
        result[key] = item
    return result


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list | tuple):
        raise RegistryError(f"{label} must be an array")
    return list(value)


def _required(data: Mapping[str, object], name: str) -> object:
    if name not in data:
        raise RegistryError(f"required remote Workspace field is missing: {name}")
    return data[name]


def _required_string(data: Mapping[str, object], name: str) -> str:
    value = _required(data, name)
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"remote Workspace field {name} must be a non-blank string")
    return value


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"remote Workspace field {name} must be a non-blank string or null")
    return value


def _required_integer(data: Mapping[str, object], name: str, *, minimum: int) -> int:
    value = _required(data, name)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RegistryError(f"remote Workspace field {name} must be an integer >= {minimum}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise RegistryError(f"remote Workspace field {name} must be boolean")
    return value


def _datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise RegistryError("remote Workspace timestamp must be a string")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RegistryError("remote Workspace timestamp is invalid") from exc
    if timestamp.tzinfo is None:
        raise RegistryError("remote Workspace timestamp must include timezone")
    return timestamp


__all__ = ["_ManifestEntry", "_canonical_snapshot_checksum"]
