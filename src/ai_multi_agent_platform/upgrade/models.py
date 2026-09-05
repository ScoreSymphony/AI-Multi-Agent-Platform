"""Canonical upgrade/migration models for issue #41."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class MigrationStatus(StrEnum):
    STARTED = "started"
    APPLIED = "applied"
    FAILED = "failed"


class CheckSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class RollbackMode(StrEnum):
    REVERSIBLE = "reversible"
    CODE_ONLY_BEFORE_MIGRATION = "code_only_before_migration"
    RESTORE_REQUIRED = "restore_required"


@dataclass(frozen=True, slots=True)
class VersionSnapshot:
    """Independent version dimensions for one platform installation/release."""

    platform_release: str
    domain_schema: str
    api: str
    migration_revision: str
    plugin_manifest: str
    portable_format: str
    template_schema: str
    backup_format: str
    worker_protocol: str
    message_protocol: str
    adapter_versions: Mapping[str, str] = field(default_factory=dict)
    plugin_interface_versions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter_versions", MappingProxyType(dict(self.adapter_versions)))
        object.__setattr__(
            self,
            "plugin_interface_versions",
            MappingProxyType(dict(self.plugin_interface_versions)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "platform_release": self.platform_release,
            "domain_schema": self.domain_schema,
            "api": self.api,
            "migration_revision": self.migration_revision,
            "plugin_manifest": self.plugin_manifest,
            "portable_format": self.portable_format,
            "template_schema": self.template_schema,
            "backup_format": self.backup_format,
            "worker_protocol": self.worker_protocol,
            "message_protocol": self.message_protocol,
            "adapter_versions": dict(self.adapter_versions),
            "plugin_interface_versions": dict(self.plugin_interface_versions),
        }


@dataclass(frozen=True, slots=True)
class MigrationRecord:
    revision: str
    checksum: str
    from_schema: str
    to_schema: str
    status: MigrationStatus
    started_at: str
    finished_at: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "checksum": self.checksum,
            "from_schema": self.from_schema,
            "to_schema": self.to_schema,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class PreflightCheck:
    code: str
    severity: CheckSeverity
    message: str
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class PreflightReport:
    current: VersionSnapshot
    target: VersionSnapshot
    planned_revisions: tuple[str, ...]
    checks: tuple[PreflightCheck, ...]
    backup_required: bool
    maintenance_required: bool

    @property
    def ok(self) -> bool:
        return not any(check.severity is CheckSeverity.ERROR for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "current": self.current.to_dict(),
            "target": self.target.to_dict(),
            "planned_revisions": list(self.planned_revisions),
            "checks": [check.to_dict() for check in self.checks],
            "backup_required": self.backup_required,
            "maintenance_required": self.maintenance_required,
        }


@dataclass(frozen=True, slots=True)
class UpgradeResult:
    started_at: str
    finished_at: str
    previous: VersionSnapshot
    current: VersionSnapshot
    applied_revisions: tuple[str, ...]
    backup_dir: str | None
    rollback_mode: RollbackMode

    def to_dict(self) -> dict[str, object]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "previous": self.previous.to_dict(),
            "current": self.current.to_dict(),
            "applied_revisions": list(self.applied_revisions),
            "backup_dir": self.backup_dir,
            "rollback_mode": self.rollback_mode.value,
        }
