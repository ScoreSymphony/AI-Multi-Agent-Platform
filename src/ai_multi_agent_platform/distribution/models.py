"""Canonical metadata for optional distribution registries (issue #81)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

_VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,2}$")
_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RegistryItemType(StrEnum):
    AGENT = "agent"
    AGENT_TEAM = "agent_team"
    TOOL = "tool"
    PLUGIN = "plugin"
    WORKFLOW = "workflow"
    TEMPLATE = "template"
    MODEL_CONFIGURATION = "model_configuration"
    CONNECTOR = "connector"
    EVALUATION = "evaluation"
    DOCUMENTATION = "documentation"


class TrustStatus(StrEnum):
    UNTRUSTED = "untrusted"
    REVIEWED = "reviewed"
    TRUSTED = "trusted"
    LOCAL = "local"


class DistributionRoute(StrEnum):
    PLUGIN = "plugin"
    PORTABLE_IMPORT = "portable_import"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class VersionRange:
    minimum: str | None = None
    maximum: str | None = None

    def __post_init__(self) -> None:
        if self.minimum is not None:
            _require_version(self.minimum, "minimum")
        if self.maximum is not None:
            _require_version(self.maximum, "maximum")
        if self.minimum and self.maximum and version_key(self.minimum) > version_key(self.maximum):
            raise ValueError("minimum platform version must not exceed maximum")

    def contains(self, version: str) -> bool:
        candidate = version_key(version)
        return not (
            (self.minimum is not None and candidate < version_key(self.minimum))
            or (self.maximum is not None and candidate > version_key(self.maximum))
        )


@dataclass(frozen=True, slots=True)
class RegistryDependency:
    item_id: str
    version_range: VersionRange = field(default_factory=VersionRange)
    optional: bool = False

    def __post_init__(self) -> None:
        _require_id(self.item_id, "dependency item_id")


@dataclass(frozen=True, slots=True)
class RegistrySource:
    repository: str
    package_reference: str
    revision: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.repository, "source repository")
        _require_text(self.package_reference, "package reference")


@dataclass(frozen=True, slots=True)
class ArtifactIntegrity:
    sha256: str | None = None
    signature: str | None = None
    signature_key_id: str | None = None

    def __post_init__(self) -> None:
        if self.sha256 is not None and not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("sha256 must be a lowercase 64-character hex digest")
        if self.signature_key_id is not None and self.signature is None:
            raise ValueError("signature_key_id requires signature metadata")


def version_key(value: str) -> tuple[int, int, int]:
    _require_version(value, "version")
    parts = [int(part) for part in value.split(".")]
    parts.extend([0] * (3 - len(parts)))
    return parts[0], parts[1], parts[2]


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-blank")


def _require_id(value: str, field_name: str) -> None:
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} has invalid canonical ID syntax")


def _require_version(value: str, field_name: str) -> None:
    if not _VERSION_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a one-to-three-part numeric dotted version")
