"""Reference application-release repositories."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    ApplicationArtifact,
    ApplicationRelease,
    BuildSpecification,
    BuildTarget,
    BuildTargetState,
    BuildTargetStatus,
    GateEvidence,
    GateStatus,
    PackageType,
    ReleaseChannel,
    ReleaseStatus,
    ReleaseVisibility,
)

APPLICATION_RELEASE_REPOSITORY_SCHEMA_VERSION = "1"


class InMemoryApplicationReleaseRepository:
    """Deterministic optimistic-concurrency repository for tests and composition."""

    def __init__(self) -> None:
        self._items: dict[str, ApplicationRelease] = {}
        self._lock = asyncio.Lock()

    async def get(self, release_id: str) -> ApplicationRelease:
        try:
            return self._items[release_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"application release not found: {release_id}",
            ) from exc

    async def list(self) -> tuple[ApplicationRelease, ...]:
        return tuple(sorted(self._items.values(), key=lambda item: item.created_at))

    async def find_version(
        self,
        application_id: str,
        version: str,
        channel: str,
    ) -> ApplicationRelease | None:
        for release in self._items.values():
            if (
                release.application_id == application_id
                and release.version == version
                and release.channel.value == channel
            ):
                return release
        return None

    async def find_run(self, run_id: str) -> ApplicationRelease | None:
        matches = [
            release
            for release in self._items.values()
            if any(target.run_id == run_id for target in release.targets)
        ]
        if len(matches) > 1:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical build Run belongs to more than one application release",
            )
        return matches[0] if matches else None

    async def save(
        self,
        release: ApplicationRelease,
        *,
        expected_revision: int | None,
    ) -> ApplicationRelease:
        async with self._lock:
            current = self._items.get(release.release_id)
            if current is None:
                if expected_revision not in {None, 0}:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "application release revision conflict",
                    )
            elif expected_revision is None or current.revision != expected_revision:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "application release revision conflict",
                )
            for other in self._items.values():
                if other.release_id == release.release_id:
                    continue
                if (
                    other.application_id == release.application_id
                    and other.version == release.version
                    and other.channel is release.channel
                ):
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "application release version already exists",
                    )
                if any(
                    target.run_id is not None
                    and target.run_id == candidate.run_id
                    and candidate.run_id is not None
                    for target in other.targets
                    for candidate in release.targets
                ):
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "canonical build Run cannot belong to multiple application releases",
                    )
            self._items[release.release_id] = release
            return release


class JsonApplicationReleaseRepository(InMemoryApplicationReleaseRepository):
    """Durable reference repository using an atomically replaced JSON snapshot."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        self._persistence_lock = asyncio.Lock()
        if self.path.exists():
            self._restore()

    async def save(
        self,
        release: ApplicationRelease,
        *,
        expected_revision: int | None,
    ) -> ApplicationRelease:
        async with self._persistence_lock:
            previous = self._items.get(release.release_id)
            saved = await super().save(release, expected_revision=expected_revision)
            try:
                self._write()
            except Exception:
                if previous is None:
                    self._items.pop(release.release_id, None)
                else:
                    self._items[release.release_id] = previous
                raise
            return saved

    def _write(self) -> None:
        document: dict[str, JsonValue] = {
            "schema_version": APPLICATION_RELEASE_REPOSITORY_SCHEMA_VERSION,
            "releases": [_encode(item) for item in self._items.values()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _restore(self) -> None:
        raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        document = _json_object(raw, "application release repository document")
        version = _required_string(document, "schema_version")
        if version != APPLICATION_RELEASE_REPOSITORY_SCHEMA_VERSION:
            raise ValueError(
                "unsupported application release repository schema version: "
                f"{version!r}; expected {APPLICATION_RELEASE_REPOSITORY_SCHEMA_VERSION!r}"
            )
        identities: set[tuple[str, str, ReleaseChannel]] = set()
        run_ids: set[str] = set()
        for raw_release in _required_array(document, "releases"):
            release = _release(raw_release)
            if release.release_id in self._items:
                raise ValueError("duplicate application release id in repository")
            identity = (release.application_id, release.version, release.channel)
            if identity in identities:
                raise ValueError("duplicate application release version in repository")
            identities.add(identity)
            release_run_ids = {
                target.run_id for target in release.targets if target.run_id is not None
            }
            if run_ids & release_run_ids:
                raise ValueError("duplicate application build Run across persisted releases")
            run_ids.update(release_run_ids)
            self._items[release.release_id] = release


def _encode(value: Any) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return _encode(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _encode(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        encoded: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("application release persistence requires string mapping keys")
            encoded[key] = _encode(item)
        return encoded
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_encode(item) for item in value]
    raise TypeError(f"unsupported application release persistence value: {type(value).__name__}")


def _release(value: JsonValue) -> ApplicationRelease:
    data = _json_object(value, "application release")
    return ApplicationRelease(
        application_id=_required_string(data, "application_id"),
        display_name=_required_string(data, "display_name"),
        version=_required_string(data, "version"),
        channel=ReleaseChannel(_required_string(data, "channel")),
        visibility=ReleaseVisibility(_required_string(data, "visibility")),
        project_id=_required_string(data, "project_id"),
        workspace_id=_required_string(data, "workspace_id"),
        workspace_snapshot_id=_required_string(data, "workspace_snapshot_id"),
        workspace_content_checksum=_required_string(data, "workspace_content_checksum"),
        source_revision=_required_string(data, "source_revision"),
        build_specification=_build_specification(data.get("build_specification")),
        creator_ref=_required_string(data, "creator_ref"),
        release_id=_required_string(data, "release_id"),
        status=ReleaseStatus(_required_string(data, "status")),
        targets=tuple(_build_target_state(item) for item in _required_array(data, "targets")),
        artifacts=tuple(_application_artifact(item) for item in _required_array(data, "artifacts")),
        gates=tuple(_gate_evidence(item) for item in _required_array(data, "gates")),
        release_notes=_optional_string(data, "release_notes"),
        publisher_id=_optional_string(data, "publisher_id"),
        release_url=_optional_string(data, "release_url"),
        latest_url=_optional_string(data, "latest_url"),
        external_metadata=_json_object(data.get("external_metadata"), "external_metadata"),
        previous_release_id=_optional_string(data, "previous_release_id"),
        created_at=_datetime(data.get("created_at"), "created_at"),
        published_at=_optional_datetime(data.get("published_at"), "published_at"),
        revision=_required_int(data, "revision"),
        schema_version=_required_string(data, "schema_version"),
    )


def _build_specification(value: JsonValue | None) -> BuildSpecification:
    data = _json_object(value, "build specification")
    return BuildSpecification(
        command=_string_tuple(data.get("command"), "command"),
        targets=tuple(_build_target(item) for item in _required_array(data, "targets")),
        spec_id=_required_string(data, "spec_id"),
        revision=_required_int(data, "revision"),
        source_path=_optional_string(data, "source_path"),
        workflow_ref=_optional_string(data, "workflow_ref"),
        pre_build_checks=_string_tuple(data.get("pre_build_checks"), "pre_build_checks"),
        test_gates=_string_tuple(data.get("test_gates"), "test_gates"),
        post_build_checks=_string_tuple(data.get("post_build_checks"), "post_build_checks"),
        required_capabilities=_string_tuple(
            data.get("required_capabilities"), "required_capabilities"
        ),
        resource_hints=_json_object(data.get("resource_hints"), "resource_hints"),
        secret_references=_string_tuple(data.get("secret_references"), "secret_references"),
    )


def _build_target(value: JsonValue | None) -> BuildTarget:
    data = _json_object(value, "build target")
    return BuildTarget(
        target_id=_required_string(data, "target_id"),
        os_name=_required_string(data, "os_name"),
        architecture=_required_string(data, "architecture"),
        package_type=PackageType(_required_string(data, "package_type")),
        output_path=_required_string(data, "output_path"),
        required_capabilities=_string_tuple(
            data.get("required_capabilities"), "required_capabilities"
        ),
    )


def _build_target_state(value: JsonValue) -> BuildTargetState:
    data = _json_object(value, "build target state")
    return BuildTargetState(
        target=_build_target(data.get("target")),
        status=BuildTargetStatus(_required_string(data, "status")),
        task_id=_optional_string(data, "task_id"),
        run_id=_optional_string(data, "run_id"),
        failure_reason=_optional_string(data, "failure_reason"),
    )


def _application_artifact(value: JsonValue) -> ApplicationArtifact:
    data = _json_object(value, "application artifact")
    return ApplicationArtifact(
        artifact_id=_required_string(data, "artifact_id"),
        file_id=_required_string(data, "file_id"),
        target_id=_required_string(data, "target_id"),
        filename=_required_string(data, "filename"),
        package_type=PackageType(_required_string(data, "package_type")),
        media_type=_required_string(data, "media_type"),
        sha256=_required_string(data, "sha256"),
        build_task_id=_required_string(data, "build_task_id"),
        build_run_id=_required_string(data, "build_run_id"),
        evidence_refs=_string_tuple(data.get("evidence_refs"), "evidence_refs"),
        download_url=_optional_string(data, "download_url"),
        external_metadata=_json_object(data.get("external_metadata"), "external_metadata"),
    )


def _gate_evidence(value: JsonValue) -> GateEvidence:
    data = _json_object(value, "gate evidence")
    return GateEvidence(
        name=_required_string(data, "name"),
        status=GateStatus(_required_string(data, "status")),
        evidence_refs=_string_tuple(data.get("evidence_refs"), "evidence_refs"),
        details=_json_object(data.get("details"), "details"),
    )


def _json_object(value: object, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object with string keys")
    return cast(dict[str, JsonValue], value)


def _required_array(data: dict[str, JsonValue], field: str) -> list[JsonValue]:
    value = data.get(field)
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def _required_string(data: dict[str, JsonValue], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value


def _optional_string(data: dict[str, JsonValue], field: str) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string when provided")
    return value


def _required_int(data: dict[str, JsonValue], field: str) -> int:
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _string_tuple(value: JsonValue | None, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be an array of strings")
    return tuple(cast(list[str], value))


def _datetime(value: JsonValue | None, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO datetime")
    return datetime.fromisoformat(value)


def _optional_datetime(value: JsonValue | None, field: str) -> datetime | None:
    if value is None:
        return None
    return _datetime(value, field)


__all__ = [
    "APPLICATION_RELEASE_REPOSITORY_SCHEMA_VERSION",
    "InMemoryApplicationReleaseRepository",
    "JsonApplicationReleaseRepository",
]
