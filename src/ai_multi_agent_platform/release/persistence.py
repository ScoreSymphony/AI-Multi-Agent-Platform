"""Durable advisory upstream-discovery reports for issue #42."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

from .discovery import (
    UpdateCandidate,
    UpdateClassification,
    UpdateDiscoveryError,
    UpdateDiscoveryReport,
    UpdateDisposition,
)
from .models import GateStatus

DISCOVERY_REPORT_STATE_SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class StoredDiscoveryReport:
    reviewed_at: str
    report: UpdateDiscoveryReport

    def to_dict(self) -> dict[str, object]:
        return {
            "state_schema": DISCOVERY_REPORT_STATE_SCHEMA_VERSION,
            "reviewed_at": self.reviewed_at,
            "report": self.report.to_dict(),
        }


class JsonDiscoveryReportStore:
    """Atomic deployment-local store for the latest reviewed advisory report."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def for_data_dir(cls, data_dir: str | Path) -> JsonDiscoveryReportStore:
        return cls(Path(data_dir) / "db" / "release-upstream-discovery.json")

    def read(self) -> StoredDiscoveryReport | None:
        if not self.path.is_file():
            return None
        try:
            raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UpdateDiscoveryError(f"cannot read persisted discovery report: {exc}") from exc
        if not isinstance(raw, dict):
            raise UpdateDiscoveryError("persisted discovery report must be a JSON object")
        document = cast(dict[str, object], raw)
        if _string(document, "state_schema") != DISCOVERY_REPORT_STATE_SCHEMA_VERSION:
            raise UpdateDiscoveryError("unsupported persisted discovery report state_schema")
        reviewed_at = _timestamp(document, "reviewed_at")
        report = _decode_report(_mapping(document, "report"))
        return StoredDiscoveryReport(reviewed_at=reviewed_at, report=report)

    def write(self, stored: StoredDiscoveryReport) -> None:
        _validate_timestamp(stored.reviewed_at, "reviewed_at")
        _decode_report(stored.report.to_dict())
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(stored.to_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            raise UpdateDiscoveryError(f"cannot persist discovery report: {exc}") from exc


def _decode_report(value: dict[str, object]) -> UpdateDiscoveryReport:
    try:
        mode = UpdateDisposition(_string(value, "mode"))
    except ValueError as exc:
        raise UpdateDiscoveryError("persisted discovery report mode is invalid") from exc
    observed_at = _optional_timestamp(value, "observed_at")
    candidates = tuple(_decode_candidate(item) for item in _object_list(value, "candidates"))
    update_available = value.get("update_available")
    if not isinstance(update_available, bool):
        raise UpdateDiscoveryError("persisted discovery update_available must be a boolean")
    report = UpdateDiscoveryReport(
        mode=mode,
        observed_at=observed_at,
        candidates=candidates,
    )
    if report.update_available != update_available:
        raise UpdateDiscoveryError(
            "persisted discovery update_available does not match candidate dispositions"
        )
    return report


def _decode_candidate(value: dict[str, object]) -> UpdateCandidate:
    try:
        disposition = UpdateDisposition(_string(value, "disposition"))
        classifications = tuple(
            UpdateClassification(item) for item in _string_list(value, "classifications")
        )
    except ValueError as exc:
        raise UpdateDiscoveryError("persisted discovery candidate enum value is invalid") from exc
    manual_review_required = value.get("manual_review_required")
    if not isinstance(manual_review_required, bool):
        raise UpdateDiscoveryError("persisted discovery manual_review_required must be a boolean")

    validation_raw = value.get("validation")
    validation: dict[str, GateStatus] | None
    if validation_raw is None:
        validation = None
    elif isinstance(validation_raw, dict):
        validation = {}
        for name, status in validation_raw.items():
            if not isinstance(name, str) or not isinstance(status, str):
                raise UpdateDiscoveryError(
                    "persisted discovery validation must map strings to gate statuses"
                )
            try:
                validation[name] = GateStatus(status)
            except ValueError as exc:
                raise UpdateDiscoveryError(
                    f"persisted discovery validation gate {name!r} is invalid"
                ) from exc
    else:
        raise UpdateDiscoveryError("persisted discovery validation must be null or an object")

    return UpdateCandidate(
        component=_string(value, "component"),
        source_url=_string(value, "source_url"),
        current_revision=_string(value, "current_revision"),
        candidate_revision=_optional_string(value, "candidate_revision"),
        disposition=disposition,
        classifications=classifications,
        manual_review_required=manual_review_required,
        reasons=tuple(_string_list(value, "reasons")),
        release_ref=_optional_string(value, "release_ref"),
        published_at=_optional_timestamp(value, "published_at"),
        validation=validation,
    )


def _mapping(value: dict[str, object], name: str) -> dict[str, object]:
    raw = value.get(name)
    if not isinstance(raw, dict):
        raise UpdateDiscoveryError(f"{name} must be an object")
    return cast(dict[str, object], raw)


def _object_list(value: dict[str, object], name: str) -> list[dict[str, object]]:
    raw = value.get(name)
    if not isinstance(raw, list):
        raise UpdateDiscoveryError(f"{name} must be an array")
    result: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise UpdateDiscoveryError(f"{name} entries must be objects")
        result.append(cast(dict[str, object], item))
    return result


def _string(value: dict[str, object], name: str) -> str:
    raw = value.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise UpdateDiscoveryError(f"{name} must be a non-empty string")
    return raw


def _optional_string(value: dict[str, object], name: str) -> str | None:
    raw = value.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise UpdateDiscoveryError(f"{name} must be null or a non-empty string")
    return raw


def _string_list(value: dict[str, object], name: str) -> list[str]:
    raw = value.get(name)
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise UpdateDiscoveryError(f"{name} must be an array of non-empty strings")
    return cast(list[str], raw)


def _timestamp(value: dict[str, object], name: str) -> str:
    raw = _string(value, name)
    _validate_timestamp(raw, name)
    return raw


def _optional_timestamp(value: dict[str, object], name: str) -> str | None:
    raw = value.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise UpdateDiscoveryError(f"{name} must be null or an RFC3339 timestamp")
    _validate_timestamp(raw, name)
    return raw


def _validate_timestamp(value: str, name: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise UpdateDiscoveryError(f"{name} must be an RFC3339 timestamp") from exc
    if "T" not in value or parsed.utcoffset() is None:
        raise UpdateDiscoveryError(f"{name} must be an RFC3339 timestamp with timezone")
