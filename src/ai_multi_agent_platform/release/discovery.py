"""Advisory-only upstream update discovery and classification."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from importlib.resources import files
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.upgrade.models import VersionSnapshot

from .adoption import (
    UpdateValidationEvidenceError,
    UpdateValidationEvidenceSet,
    validate_update_adoption_evidence,
)
from .models import GateStatus

COMPATIBILITY_INVENTORY_SCHEMA_VERSION = "2"
UPDATE_OBSERVATION_SCHEMA_VERSION = "1"
REQUIRED_ADOPTION_GATES = frozenset(
    {
        "adapter_contract_tests",
        "eval_regression",
        "security",
        "compatibility_review",
    }
)


class UpdateClassification(StrEnum):
    SECURITY = "security"
    BUGFIX = "bugfix"
    FEATURE = "feature"
    BREAKING_API = "breaking_api"
    LICENSE_GOVERNANCE = "license_governance"
    IRRELEVANT = "irrelevant"
    UNKNOWN = "unknown"


class UpdateDisposition(StrEnum):
    CURRENT = "current"
    UPDATE_AVAILABLE = "update_available"
    MANUAL_REVIEW = "manual_review"
    BLOCKED = "blocked"
    NOT_CHECKED = "not_checked"
    DISABLED = "disabled"
    OFFLINE = "offline"


@dataclass(frozen=True, slots=True)
class UpstreamInventoryEntry:
    component: str
    source_url: str
    revision: str
    compatibility_status: str
    integration_mode: str
    boundary: str
    license: str
    last_checked_at: str
    latest_known_revision: str
    update_risk: str
    local_modifications: bool
    patches: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "source_url": self.source_url,
            "revision": self.revision,
            "status": self.compatibility_status,
            "integration_mode": self.integration_mode,
            "boundary": self.boundary,
            "license": self.license,
            "last_checked_at": self.last_checked_at,
            "latest_known_revision": self.latest_known_revision,
            "update_risk": self.update_risk,
            "local_modifications": self.local_modifications,
            "patches": list(self.patches),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class CompatibilityInventory:
    versions: VersionSnapshot
    last_reviewed_at: str
    entries: tuple[UpstreamInventoryEntry, ...]
    generated_from: str = "upstream/*.yaml"
    schema_version: str = COMPATIBILITY_INVENTORY_SCHEMA_VERSION

    @property
    def platform_release(self) -> str:
        return self.versions.platform_release

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "platform_release": self.platform_release,
            "versions": self.versions.to_dict(),
            "generated_from": self.generated_from,
            "last_reviewed_at": self.last_reviewed_at,
            "components": [entry.to_dict() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class ObservedUpstream:
    component: str
    source_url: str
    revision: str
    license: str
    classifications: tuple[UpdateClassification, ...] = ()
    release_ref: str | None = None
    published_at: str | None = None
    patch_conflicts: tuple[str, ...] = ()
    validation: Mapping[str, GateStatus] | None = None


@dataclass(frozen=True, slots=True)
class UpdateCandidate:
    component: str
    source_url: str
    current_revision: str
    candidate_revision: str | None
    disposition: UpdateDisposition
    classifications: tuple[UpdateClassification, ...]
    manual_review_required: bool
    reasons: tuple[str, ...]
    release_ref: str | None = None
    published_at: str | None = None
    validation: Mapping[str, GateStatus] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "source_url": self.source_url,
            "current_revision": self.current_revision,
            "candidate_revision": self.candidate_revision,
            "disposition": self.disposition.value,
            "classifications": [item.value for item in self.classifications],
            "manual_review_required": self.manual_review_required,
            "reasons": list(self.reasons),
            "release_ref": self.release_ref,
            "published_at": self.published_at,
            "validation": None
            if self.validation is None
            else {name: status.value for name, status in self.validation.items()},
        }


@dataclass(frozen=True, slots=True)
class UpdateDiscoveryReport:
    mode: UpdateDisposition
    observed_at: str | None
    candidates: tuple[UpdateCandidate, ...]

    @property
    def update_available(self) -> bool:
        return any(
            item.disposition
            in {
                UpdateDisposition.UPDATE_AVAILABLE,
                UpdateDisposition.MANUAL_REVIEW,
                UpdateDisposition.BLOCKED,
            }
            for item in self.candidates
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "observed_at": self.observed_at,
            "update_available": self.update_available,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


class UpdateDiscoveryError(ValueError):
    """Raised when compatibility inventory or observation input is malformed."""


def load_compatibility_inventory(path: str | Path | None = None) -> CompatibilityInventory:
    if path is None:
        text = (
            files("ai_multi_agent_platform.release")
            .joinpath("compatibility.json")
            .read_text(encoding="utf-8")
        )
    else:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise UpdateDiscoveryError(f"cannot read compatibility inventory: {exc}") from exc
    raw = _json_object(text, "compatibility inventory")
    if _string(raw, "schema_version") != COMPATIBILITY_INVENTORY_SCHEMA_VERSION:
        raise UpdateDiscoveryError("unsupported compatibility inventory schema_version")
    versions = _decode_version_snapshot(_mapping(raw, "versions"))
    platform_release = _string(raw, "platform_release")
    if versions.platform_release != platform_release:
        raise UpdateDiscoveryError(
            "compatibility inventory platform_release must match versions.platform_release"
        )
    return CompatibilityInventory(
        schema_version=COMPATIBILITY_INVENTORY_SCHEMA_VERSION,
        versions=versions,
        generated_from=_string(raw, "generated_from"),
        last_reviewed_at=_string(raw, "last_reviewed_at"),
        entries=tuple(_decode_inventory_entry(item) for item in _object_list(raw, "components")),
    )


def load_observations(path: str | Path) -> tuple[str, tuple[ObservedUpstream, ...]]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise UpdateDiscoveryError(f"cannot read upstream observations: {exc}") from exc
    raw = _json_object(text, "upstream observations")
    if _string(raw, "schema_version") != UPDATE_OBSERVATION_SCHEMA_VERSION:
        raise UpdateDiscoveryError("unsupported upstream observation schema_version")
    observed_at = _string(raw, "observed_at")
    return observed_at, tuple(_decode_observation(item) for item in _object_list(raw, "components"))


def evaluate_update_candidates(
    inventory: CompatibilityInventory,
    observations: tuple[ObservedUpstream, ...] = (),
    *,
    observed_at: str | None = None,
    enabled: bool = True,
    offline: bool = False,
) -> UpdateDiscoveryReport:
    if not enabled:
        return UpdateDiscoveryReport(
            mode=UpdateDisposition.DISABLED,
            observed_at=None,
            candidates=tuple(
                _not_checked(entry, UpdateDisposition.DISABLED) for entry in inventory.entries
            ),
        )
    if offline:
        return UpdateDiscoveryReport(
            mode=UpdateDisposition.OFFLINE,
            observed_at=observed_at,
            candidates=tuple(
                _not_checked(entry, UpdateDisposition.OFFLINE) for entry in inventory.entries
            ),
        )

    by_component = {item.component: item for item in observations}
    duplicates = sorted(
        component
        for component in by_component
        if sum(item.component == component for item in observations) > 1
    )
    if duplicates:
        raise UpdateDiscoveryError(f"duplicate observations: {', '.join(duplicates)}")

    candidates = tuple(
        _evaluate_candidate(entry, by_component.get(entry.component)) for entry in inventory.entries
    )
    return UpdateDiscoveryReport(
        mode=UpdateDisposition.CURRENT,
        observed_at=observed_at,
        candidates=candidates,
    )


def record_reviewed_candidate(
    inventory: CompatibilityInventory,
    candidate: UpdateCandidate,
    *,
    compatibility_status: str,
    reviewed_at: str,
    validation_evidence: UpdateValidationEvidenceSet,
    manual_review_approved: bool = False,
) -> CompatibilityInventory:
    """Return updated compatibility metadata after an explicit review decision.

    This function is pure: it does not mutate a production pin, write a file, merge a branch,
    or deploy anything. Callers remain responsible for the dedicated update PR and release gates.
    """

    if candidate.disposition in {
        UpdateDisposition.BLOCKED,
        UpdateDisposition.NOT_CHECKED,
        UpdateDisposition.DISABLED,
        UpdateDisposition.OFFLINE,
    }:
        raise UpdateDiscoveryError(
            f"candidate {candidate.component!r} cannot be recorded from "
            f"{candidate.disposition.value!r}"
        )
    if candidate.candidate_revision is None:
        raise UpdateDiscoveryError("candidate revision is missing")

    try:
        validate_update_adoption_evidence(
            component=candidate.component,
            candidate_revision=candidate.candidate_revision,
            validation=candidate.validation,
            evidence=validation_evidence,
            required_gates=REQUIRED_ADOPTION_GATES,
        )
    except UpdateValidationEvidenceError as exc:
        raise UpdateDiscoveryError(str(exc)) from exc

    if candidate.manual_review_required and not manual_review_approved:
        raise UpdateDiscoveryError("manual review approval is required before recording candidate")
    if compatibility_status not in {
        "supported",
        "tested",
        "experimental",
        "deprecated",
        "blocked",
    }:
        raise UpdateDiscoveryError(f"invalid compatibility status: {compatibility_status!r}")

    found = False
    entries: list[UpstreamInventoryEntry] = []
    for entry in inventory.entries:
        if entry.component != candidate.component:
            entries.append(entry)
            continue
        found = True
        entries.append(
            replace(
                entry,
                revision=candidate.candidate_revision,
                compatibility_status=compatibility_status,
                latest_known_revision=candidate.candidate_revision,
                last_checked_at=reviewed_at,
            )
        )
    if not found:
        raise UpdateDiscoveryError(
            f"candidate component not present in inventory: {candidate.component}"
        )
    return replace(
        inventory,
        last_reviewed_at=reviewed_at,
        entries=tuple(entries),
    )


def _evaluate_candidate(
    current: UpstreamInventoryEntry,
    observed: ObservedUpstream | None,
) -> UpdateCandidate:
    if observed is None:
        return _not_checked(current, UpdateDisposition.NOT_CHECKED)

    reasons: list[str] = []
    classifications = set(observed.classifications)
    if observed.source_url != current.source_url:
        classifications.add(UpdateClassification.LICENSE_GOVERNANCE)
        reasons.append("canonical source changed; provenance/governance review required")
    if observed.license != current.license:
        classifications.add(UpdateClassification.LICENSE_GOVERNANCE)
        reasons.append("license metadata changed; manual license review required")

    revision_changed = observed.revision != current.revision
    if not revision_changed and not reasons and not observed.patch_conflicts:
        return UpdateCandidate(
            component=current.component,
            source_url=current.source_url,
            current_revision=current.revision,
            candidate_revision=observed.revision,
            disposition=UpdateDisposition.CURRENT,
            classifications=tuple(sorted(classifications, key=lambda item: item.value)),
            manual_review_required=False,
            reasons=(),
            release_ref=observed.release_ref,
            published_at=observed.published_at,
            validation=observed.validation,
        )

    if revision_changed:
        reasons.append("observed immutable revision differs from the production baseline")
        if not classifications:
            classifications.add(UpdateClassification.UNKNOWN)
            reasons.append("change type is unknown; manual review required")

    if observed.patch_conflicts:
        reasons.append("local patch conflicts: " + ", ".join(observed.patch_conflicts))

    failed_validation = sorted(
        name for name, status in (observed.validation or {}).items() if status is GateStatus.FAILED
    )
    if failed_validation:
        reasons.append("candidate validation failed: " + ", ".join(failed_validation))

    manual_classes = {
        UpdateClassification.BREAKING_API,
        UpdateClassification.LICENSE_GOVERNANCE,
        UpdateClassification.UNKNOWN,
    }
    manual_review = bool(classifications.intersection(manual_classes))
    blocked = bool(observed.patch_conflicts or failed_validation)
    disposition = (
        UpdateDisposition.BLOCKED
        if blocked
        else UpdateDisposition.MANUAL_REVIEW
        if manual_review
        else UpdateDisposition.UPDATE_AVAILABLE
    )
    return UpdateCandidate(
        component=current.component,
        source_url=current.source_url,
        current_revision=current.revision,
        candidate_revision=observed.revision,
        disposition=disposition,
        classifications=tuple(sorted(classifications, key=lambda item: item.value)),
        manual_review_required=manual_review or blocked,
        reasons=tuple(reasons),
        release_ref=observed.release_ref,
        published_at=observed.published_at,
        validation=observed.validation,
    )


def _not_checked(
    entry: UpstreamInventoryEntry,
    disposition: UpdateDisposition,
) -> UpdateCandidate:
    reason = {
        UpdateDisposition.DISABLED: "upstream discovery is disabled by operator policy",
        UpdateDisposition.OFFLINE: (
            "upstream discovery is offline; production pin remains unchanged"
        ),
        UpdateDisposition.NOT_CHECKED: "no observation was provided for this upstream",
    }[disposition]
    return UpdateCandidate(
        component=entry.component,
        source_url=entry.source_url,
        current_revision=entry.revision,
        candidate_revision=None,
        disposition=disposition,
        classifications=(),
        manual_review_required=False,
        reasons=(reason,),
    )


def _decode_version_snapshot(value: Mapping[str, object]) -> VersionSnapshot:
    return VersionSnapshot(
        platform_release=_string(value, "platform_release"),
        domain_schema=_string(value, "domain_schema"),
        api=_string(value, "api"),
        migration_revision=_string(value, "migration_revision"),
        plugin_manifest=_string(value, "plugin_manifest"),
        portable_format=_string(value, "portable_format"),
        template_schema=_string(value, "template_schema"),
        backup_format=_string(value, "backup_format"),
        worker_protocol=_string(value, "worker_protocol"),
        message_protocol=_string(value, "message_protocol"),
        adapter_versions=_string_map(value, "adapter_versions"),
        plugin_interface_versions=_string_map(value, "plugin_interface_versions"),
    )


def _decode_inventory_entry(value: Mapping[str, object]) -> UpstreamInventoryEntry:
    return UpstreamInventoryEntry(
        component=_string(value, "component"),
        source_url=_string(value, "source_url"),
        revision=_immutable_revision(value),
        compatibility_status=_string(value, "status"),
        integration_mode=_string(value, "integration_mode"),
        boundary=_string(value, "boundary"),
        license=_string(value, "license"),
        last_checked_at=_string(value, "last_checked_at"),
        latest_known_revision=_string(value, "latest_known_revision"),
        update_risk=_string(value, "update_risk"),
        local_modifications=_bool(value, "local_modifications"),
        patches=tuple(_string_list(value, "patches")),
        notes=tuple(_string_list(value, "notes")),
    )


def _decode_observation(value: Mapping[str, object]) -> ObservedUpstream:
    raw_validation = value.get("validation", {})
    if not isinstance(raw_validation, dict):
        raise UpdateDiscoveryError("validation must be an object")
    validation: dict[str, GateStatus] = {}
    for name, status in raw_validation.items():
        if not isinstance(name, str) or not isinstance(status, str):
            raise UpdateDiscoveryError("validation must map strings to gate statuses")
        try:
            validation[name] = GateStatus(status)
        except ValueError as exc:
            raise UpdateDiscoveryError(
                f"invalid validation status for {name!r}: {status!r}"
            ) from exc

    raw_classes = _string_list(value, "classifications")
    try:
        classifications = tuple(UpdateClassification(item) for item in raw_classes)
    except ValueError as exc:
        raise UpdateDiscoveryError(f"invalid update classification: {exc}") from exc

    return ObservedUpstream(
        component=_string(value, "component"),
        source_url=_string(value, "source_url"),
        revision=_immutable_revision(value),
        license=_string(value, "license"),
        classifications=classifications,
        release_ref=_optional_string(value, "release_ref"),
        published_at=_optional_string(value, "published_at"),
        patch_conflicts=tuple(_string_list(value, "patch_conflicts")),
        validation=validation,
    )


def _immutable_revision(value: Mapping[str, object]) -> str:
    revision = _string(value, "revision")
    if revision.lower() == "latest" or revision.lower().endswith(":latest") or "*" in revision:
        raise UpdateDiscoveryError("floating revision is not allowed")
    return revision


def _json_object(text: str, label: str) -> dict[str, object]:
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UpdateDiscoveryError(f"invalid {label} JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise UpdateDiscoveryError(f"{label} must be a JSON object")
    return cast(dict[str, object], raw)


def _mapping(value: Mapping[str, object], name: str) -> Mapping[str, object]:
    raw = value.get(name)
    if not isinstance(raw, dict):
        raise UpdateDiscoveryError(f"{name} must be an object")
    return cast(dict[str, object], raw)


def _object_list(value: Mapping[str, object], name: str) -> list[Mapping[str, object]]:
    raw = value.get(name)
    if not isinstance(raw, list):
        raise UpdateDiscoveryError(f"{name} must be an array")
    result: list[Mapping[str, object]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise UpdateDiscoveryError(f"{name} entries must be objects")
        result.append(cast(dict[str, object], item))
    return result


def _string_map(value: Mapping[str, object], name: str) -> dict[str, str]:
    raw = _mapping(value, name)
    result: dict[str, str] = {}
    for key, item in raw.items():
        if not isinstance(key, str) or not isinstance(item, str) or not item:
            raise UpdateDiscoveryError(f"{name} must map strings to non-empty strings")
        result[key] = item
    return result


def _string(value: Mapping[str, object], name: str) -> str:
    raw = value.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise UpdateDiscoveryError(f"{name} must be a non-empty string")
    return raw


def _optional_string(value: Mapping[str, object], name: str) -> str | None:
    raw = value.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise UpdateDiscoveryError(f"{name} must be null or a non-empty string")
    return raw


def _string_list(value: Mapping[str, object], name: str) -> list[str]:
    raw = value.get(name, [])
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise UpdateDiscoveryError(f"{name} must be an array of non-empty strings")
    return cast(list[str], raw)


def _bool(value: Mapping[str, object], name: str) -> bool:
    raw = value.get(name)
    if not isinstance(raw, bool):
        raise UpdateDiscoveryError(f"{name} must be a boolean")
    return raw
