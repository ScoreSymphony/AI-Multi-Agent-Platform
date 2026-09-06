"""Durable local state for Registry installations, updates and version pins."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .items import InstalledRegistryItem, RegistryItem
from .models import RegistryItemType, version_key

_STATE_VERSION = "2"
_SUPPORTED_STATE_VERSIONS = frozenset({"1", _STATE_VERSION})


@dataclass(frozen=True, slots=True)
class RegistryInstallationSnapshot:
    item_id: str
    version: str
    source_registry: str
    source_repository: str
    package_reference: str
    revision: str | None
    license: str
    provenance: str
    item_type: RegistryItemType | None = None
    artifact_sha256: str | None = None

    def __post_init__(self) -> None:
        InstalledRegistryItem(
            item_id=self.item_id,
            version=self.version,
            source_registry=self.source_registry,
            license=self.license,
            provenance=self.provenance,
        )
        if not self.source_repository.strip():
            raise ValueError("source_repository must be non-blank")
        if not self.package_reference.strip():
            raise ValueError("package_reference must be non-blank")
        if self.revision is not None and not self.revision.strip():
            raise ValueError("revision must be non-blank when provided")
        if self.artifact_sha256 is not None:
            if len(self.artifact_sha256) != 64:
                raise ValueError("artifact_sha256 must be a SHA-256 hex digest")
            try:
                bytes.fromhex(self.artifact_sha256)
            except ValueError as exc:
                raise ValueError("artifact_sha256 must be a SHA-256 hex digest") from exc

    def as_installed(self, *, pinned_version: str | None = None) -> InstalledRegistryItem:
        return InstalledRegistryItem(
            item_id=self.item_id,
            version=self.version,
            source_registry=self.source_registry,
            pinned_version=pinned_version,
            license=self.license,
            provenance=self.provenance,
        )


@dataclass(frozen=True, slots=True)
class RegistryInstallation:
    current: RegistryInstallationSnapshot
    pinned_version: str | None = None
    history: tuple[RegistryInstallationSnapshot, ...] = ()

    def __post_init__(self) -> None:
        if self.pinned_version is not None:
            version_key(self.pinned_version)

    def as_installed(self) -> InstalledRegistryItem:
        return self.current.as_installed(pinned_version=self.pinned_version)


class RegistryInstallationStore(Protocol):
    def list(self) -> tuple[RegistryInstallation, ...]: ...

    def get(self, item_id: str) -> RegistryInstallation | None: ...

    def record(
        self,
        item: RegistryItem,
        *,
        provider_id: str,
        artifact_sha256: str | None = None,
    ) -> RegistryInstallation: ...

    def pin(self, item_id: str, version: str) -> RegistryInstallation: ...

    def unpin(self, item_id: str) -> RegistryInstallation: ...


class JsonRegistryInstallationStore:
    """Small durable store whose state remains independent from any Registry provider."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._records: dict[str, RegistryInstallation] = {}
        self._load()

    def list(self) -> tuple[RegistryInstallation, ...]:
        return tuple(self._records[item_id] for item_id in sorted(self._records))

    def get(self, item_id: str) -> RegistryInstallation | None:
        return self._records.get(item_id)

    def record(
        self,
        item: RegistryItem,
        *,
        provider_id: str,
        artifact_sha256: str | None = None,
    ) -> RegistryInstallation:
        current = RegistryInstallationSnapshot(
            item_id=item.item_id,
            version=item.version,
            source_registry=provider_id,
            source_repository=item.source.repository,
            package_reference=item.source.package_reference,
            revision=item.source.revision,
            license=item.license,
            provenance=item.provenance,
            item_type=item.item_type,
            artifact_sha256=artifact_sha256 or item.integrity.sha256,
        )
        previous = self._records.get(item.item_id)
        history = previous.history if previous is not None else ()
        pinned_version = previous.pinned_version if previous is not None else None
        if previous is not None and previous.current != current:
            history = (*history, previous.current)
        record = RegistryInstallation(current, pinned_version=pinned_version, history=history)
        self._records[item.item_id] = record
        self._save()
        return record

    def pin(self, item_id: str, version: str) -> RegistryInstallation:
        version_key(version)
        record = self._require(item_id)
        if record.current.version != version:
            raise ValueError("registry item can only be pinned to its currently installed version")
        updated = RegistryInstallation(
            record.current, pinned_version=version, history=record.history
        )
        self._records[item_id] = updated
        self._save()
        return updated

    def unpin(self, item_id: str) -> RegistryInstallation:
        record = self._require(item_id)
        updated = RegistryInstallation(record.current, pinned_version=None, history=record.history)
        self._records[item_id] = updated
        self._save()
        return updated

    def _require(self, item_id: str) -> RegistryInstallation:
        try:
            return self._records[item_id]
        except KeyError as exc:
            raise LookupError(f"registry item {item_id!r} is not installed") from exc

    def _load(self) -> None:
        if not self._path.exists():
            return
        document = json.loads(self._path.read_text(encoding="utf-8"))
        if (
            not isinstance(document, dict)
            or document.get("version") not in _SUPPORTED_STATE_VERSIONS
        ):
            raise ValueError("unsupported registry installation state")
        raw_records = document.get("installations", [])
        if not isinstance(raw_records, list):
            raise ValueError("registry installation state must contain an installations array")
        records: dict[str, RegistryInstallation] = {}
        for raw in raw_records:
            record = _installation_from_json(raw)
            item_id = record.current.item_id
            if item_id in records:
                raise ValueError(f"duplicate registry installation state for {item_id!r}")
            records[item_id] = record
        self._records = records

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "version": _STATE_VERSION,
            "installations": [_installation_to_json(record) for record in self.list()],
        }
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self._path)


def _snapshot_to_json(snapshot: RegistryInstallationSnapshot) -> dict[str, object]:
    return {
        "item_id": snapshot.item_id,
        "version": snapshot.version,
        "source_registry": snapshot.source_registry,
        "source_repository": snapshot.source_repository,
        "package_reference": snapshot.package_reference,
        "revision": snapshot.revision,
        "license": snapshot.license,
        "provenance": snapshot.provenance,
        "item_type": snapshot.item_type.value if snapshot.item_type is not None else None,
        "artifact_sha256": snapshot.artifact_sha256,
    }


def _snapshot_from_json(value: object) -> RegistryInstallationSnapshot:
    if not isinstance(value, dict):
        raise ValueError("registry installation snapshot must be an object")
    return RegistryInstallationSnapshot(
        item_id=_string(value, "item_id"),
        version=_string(value, "version"),
        source_registry=_string(value, "source_registry"),
        source_repository=_string(value, "source_repository"),
        package_reference=_string(value, "package_reference"),
        revision=_optional_string(value, "revision"),
        license=_string(value, "license"),
        provenance=_string(value, "provenance"),
        item_type=_optional_item_type(value, "item_type"),
        artifact_sha256=_optional_string(value, "artifact_sha256"),
    )


def _installation_to_json(record: RegistryInstallation) -> dict[str, object]:
    return {
        "current": _snapshot_to_json(record.current),
        "pinned_version": record.pinned_version,
        "history": [_snapshot_to_json(snapshot) for snapshot in record.history],
    }


def _installation_from_json(value: object) -> RegistryInstallation:
    if not isinstance(value, dict):
        raise ValueError("registry installation must be an object")
    history = value.get("history", [])
    if not isinstance(history, list):
        raise ValueError("registry installation history must be an array")
    return RegistryInstallation(
        current=_snapshot_from_json(value.get("current")),
        pinned_version=_optional_string(value, "pinned_version"),
        history=tuple(_snapshot_from_json(item) for item in history),
    )


def _string(value: dict[object, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"registry installation field {key!r} must be a non-blank string")
    return result


def _optional_string(value: dict[object, object], key: str) -> str | None:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"registry installation field {key!r} must be null or non-blank string")
    return result


def _optional_item_type(
    value: dict[object, object],
    key: str,
) -> RegistryItemType | None:
    result = value.get(key)
    if result is None:
        return None
    if not isinstance(result, str):
        raise ValueError(f"registry installation field {key!r} must be null or a string")
    try:
        return RegistryItemType(result)
    except ValueError as exc:
        raise ValueError(f"registry installation field {key!r} has an invalid item type") from exc
