"""Typed, revision-bound evidence for reviewed upstream candidate adoption."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import cast

from .models import GateStatus, ReleaseEvidenceKind

UPDATE_VALIDATION_EVIDENCE_SCHEMA_VERSION = "1"

_DIGEST = re.compile(r"^(?:sha256:[0-9a-f]{64}|sha512:[0-9a-f]{128})$")
_REFERENCE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:.+$")
_DIGEST_REQUIRED_KINDS = frozenset(
    {
        ReleaseEvidenceKind.REPORT,
        ReleaseEvidenceKind.ARTIFACT,
        ReleaseEvidenceKind.ATTESTATION,
    }
)


class UpdateValidationEvidenceError(ValueError):
    """Raised when upstream candidate validation evidence is malformed or unbound."""


@dataclass(frozen=True, slots=True)
class UpdateGateEvidence:
    status: GateStatus
    kind: ReleaseEvidenceKind
    ref: str
    digest: str | None = None

    def __post_init__(self) -> None:
        if _REFERENCE.fullmatch(self.ref) is None:
            raise UpdateValidationEvidenceError(
                "update gate evidence ref must be a scheme-qualified reference"
            )
        if self.digest is not None and _DIGEST.fullmatch(self.digest) is None:
            raise UpdateValidationEvidenceError(
                "update gate evidence digest must be sha256 or sha512"
            )
        if self.kind in _DIGEST_REQUIRED_KINDS and self.digest is None:
            raise UpdateValidationEvidenceError(
                f"{self.kind.value} update gate evidence requires a cryptographic digest"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "kind": self.kind.value,
            "ref": self.ref,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class UpdateValidationEvidenceSet:
    component: str
    candidate_revision: str
    gates: Mapping[str, UpdateGateEvidence] = field(default_factory=dict)
    schema_version: str = UPDATE_VALIDATION_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.component.strip():
            raise UpdateValidationEvidenceError("evidence component must be non-empty")
        _validate_immutable_revision(self.candidate_revision)
        object.__setattr__(self, "gates", MappingProxyType(dict(self.gates)))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "component": self.component,
            "candidate_revision": self.candidate_revision,
            "gates": {name: evidence.to_dict() for name, evidence in self.gates.items()},
        }


def load_update_validation_evidence(path: str | Path) -> UpdateValidationEvidenceSet:
    try:
        raw: object = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateValidationEvidenceError(
            f"cannot read update validation evidence: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise UpdateValidationEvidenceError("update validation evidence must be a JSON object")
    document = cast(dict[str, object], raw)
    if _string(document, "schema_version") != UPDATE_VALIDATION_EVIDENCE_SCHEMA_VERSION:
        raise UpdateValidationEvidenceError("unsupported update validation evidence schema_version")

    raw_gates = _mapping(document, "gates")
    gates: dict[str, UpdateGateEvidence] = {}
    for name, item in raw_gates.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(item, dict):
            raise UpdateValidationEvidenceError("evidence gates must map names to objects")
        gate = cast(dict[str, object], item)
        try:
            status = GateStatus(_string(gate, "status"))
            kind = ReleaseEvidenceKind(_string(gate, "kind"))
        except ValueError as exc:
            raise UpdateValidationEvidenceError(
                f"invalid update gate evidence enum for {name!r}"
            ) from exc
        digest = _optional_string(gate, "digest")
        gates[name] = UpdateGateEvidence(
            status=status,
            kind=kind,
            ref=_string(gate, "ref"),
            digest=digest,
        )

    return UpdateValidationEvidenceSet(
        component=_string(document, "component"),
        candidate_revision=_string(document, "candidate_revision"),
        gates=gates,
    )


def validate_update_adoption_evidence(
    *,
    component: str,
    candidate_revision: str,
    validation: Mapping[str, GateStatus] | None,
    evidence: UpdateValidationEvidenceSet,
    required_gates: frozenset[str],
) -> None:
    if evidence.component != component:
        raise UpdateValidationEvidenceError(
            f"update evidence component {evidence.component!r} "
            f"does not match candidate {component!r}"
        )
    if evidence.candidate_revision != candidate_revision:
        raise UpdateValidationEvidenceError(
            "update validation evidence is bound to a different candidate revision"
        )

    status_map = validation or {}
    missing_status = sorted(
        name for name in required_gates if status_map.get(name) is not GateStatus.PASSED
    )
    if missing_status:
        raise UpdateValidationEvidenceError(
            "candidate adoption requires passed validation gates: " + ", ".join(missing_status)
        )

    missing_evidence = sorted(name for name in required_gates if name not in evidence.gates)
    if missing_evidence:
        raise UpdateValidationEvidenceError(
            "candidate adoption requires revision-bound evidence for gates: "
            + ", ".join(missing_evidence)
        )

    failed_evidence = sorted(
        name for name in required_gates if evidence.gates[name].status is not GateStatus.PASSED
    )
    if failed_evidence:
        raise UpdateValidationEvidenceError(
            "candidate adoption evidence must be passed for gates: " + ", ".join(failed_evidence)
        )

    mismatched_status = sorted(
        name for name in required_gates if status_map.get(name) is not evidence.gates[name].status
    )
    if mismatched_status:
        raise UpdateValidationEvidenceError(
            "candidate validation status/evidence mismatch for gates: "
            + ", ".join(mismatched_status)
        )


def _validate_immutable_revision(value: str) -> None:
    if not value.strip():
        raise UpdateValidationEvidenceError("candidate_revision must be non-empty")
    lowered = value.lower()
    if lowered == "latest" or lowered.endswith(":latest") or "*" in value:
        raise UpdateValidationEvidenceError("candidate_revision must be immutable")


def _mapping(value: Mapping[str, object], name: str) -> Mapping[str, object]:
    raw = value.get(name)
    if not isinstance(raw, dict):
        raise UpdateValidationEvidenceError(f"{name} must be an object")
    return cast(dict[str, object], raw)


def _string(value: Mapping[str, object], name: str) -> str:
    raw = value.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise UpdateValidationEvidenceError(f"{name} must be a non-empty string")
    return raw


def _optional_string(value: Mapping[str, object], name: str) -> str | None:
    raw = value.get(name)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise UpdateValidationEvidenceError(f"{name} must be null or a non-empty string")
    return raw
