"""Mutation-free upgrade preflight checks for issue #41."""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from ai_multi_agent_platform.backup import BackupError, BackupVerification, verify_backup
from ai_multi_agent_platform.plugins.models import PluginManifest

from .compatibility import (
    ExtensionCompatibilitySpec,
    FormatTranslatorRegistry,
    extension_compatibility_checks,
    format_compatibility_check,
    plugin_compatibility_checks,
)
from .migrations import (
    JsonMigrationHistoryStore,
    MigrationContext,
    MigrationError,
    MigrationRegistry,
    MigrationStep,
)
from .models import CheckSeverity, MigrationStatus, PreflightCheck, PreflightReport, VersionSnapshot
from .versioning import BASELINE_MIGRATION_REVISION

SUPPORTED_HISTORICAL_EVENT_SCHEMA_VERSIONS = frozenset({"1.0", "2.0"})
BackupVerifier = Callable[[Path], BackupVerification]


@dataclass(frozen=True, slots=True)
class PreflightRequest:
    data_dir: Path
    current: VersionSnapshot
    target: VersionSnapshot
    backup_dir: Path | None = None
    plugins: tuple[PluginManifest, ...] = ()
    required_plugin_ids: frozenset[str] = frozenset()
    expected_plugin_interfaces: Mapping[str, str] = field(default_factory=dict)
    plugin_state_migration_required: frozenset[str] = frozenset()
    plugin_state_migration_hook_available: bool = False
    adapters: tuple[ExtensionCompatibilitySpec, ...] = ()
    config_schema_versions: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    historical_event_schema_versions: frozenset[str] = frozenset()
    portable_package_versions: frozenset[str] = frozenset()
    template_package_versions: frozenset[str] = frozenset()
    minimum_free_bytes: int = 0
    resume_failed: bool = False


class UpgradePreflight:
    def __init__(
        self,
        migrations: MigrationRegistry,
        history: JsonMigrationHistoryStore,
        *,
        portable_translators: FormatTranslatorRegistry,
        template_translators: FormatTranslatorRegistry,
        backup_verifier: BackupVerifier = verify_backup,
    ) -> None:
        self.migrations = migrations
        self.history = history
        self.portable_translators = portable_translators
        self.template_translators = template_translators
        self.backup_verifier = backup_verifier

    def run(self, request: PreflightRequest) -> PreflightReport:
        checks: list[PreflightCheck] = []
        steps: tuple[MigrationStep, ...] = ()
        try:
            steps = self.migrations.plan(
                request.current.domain_schema,
                request.target.domain_schema,
            )
            checks.append(
                PreflightCheck(
                    code="migration.path.supported",
                    severity=CheckSeverity.INFO,
                    message="supported deterministic migration path found",
                    details={"revisions": [step.revision for step in steps]},
                )
            )
            checks.extend(
                _target_migration_revision_checks(
                    request.current,
                    request.target,
                    steps,
                )
            )
        except MigrationError as exc:
            checks.append(
                PreflightCheck(
                    code="migration.path.unsupported",
                    severity=CheckSeverity.ERROR,
                    message=str(exc),
                )
            )

        checks.extend(_current_migration_revision_checks(request.current, self.history))
        checks.extend(_version_checks(request.current, request.target))
        checks.extend(_storage_checks(request.data_dir, request.minimum_free_bytes))
        checks.extend(_migration_precondition_checks(steps, self.history, request.data_dir))

        unresolved = self.history.unresolved()
        if unresolved is not None:
            matching = next((step for step in steps if step.revision == unresolved.revision), None)
            resumable = request.resume_failed and matching is not None and matching.restart_safe
            state_name = "interrupted" if unresolved.status is MigrationStatus.STARTED else "failed"
            checks.append(
                PreflightCheck(
                    code=(
                        f"migration.{state_name}.resumable"
                        if resumable
                        else f"migration.{state_name}.unresolved"
                    ),
                    severity=CheckSeverity.WARNING if resumable else CheckSeverity.ERROR,
                    message=(
                        f"{state_name} migration {unresolved.revision} will be explicitly resumed"
                        if resumable
                        else (
                            f"unresolved {state_name} migration {unresolved.revision} "
                            "blocks upgrade"
                        )
                    ),
                    details={
                        "status": unresolved.status.value,
                        "error": unresolved.error,
                    },
                )
            )

        checks.extend(
            plugin_compatibility_checks(
                request.plugins,
                target_platform=request.target.platform_release,
                expected_interfaces=request.expected_plugin_interfaces,
                required_plugin_ids=request.required_plugin_ids,
            )
        )
        checks.extend(_plugin_state_migration_checks(request))
        checks.extend(
            extension_compatibility_checks(
                request.adapters,
                target_platform=request.target.platform_release,
            )
        )
        checks.extend(_configuration_checks(request.config_schema_versions))
        checks.extend(_historical_event_checks(request.historical_event_schema_versions))

        for version in sorted(request.portable_package_versions):
            checks.append(
                format_compatibility_check(
                    kind="portable",
                    source_version=version,
                    target_version=request.target.portable_format,
                    translators=self.portable_translators,
                )
            )
        for version in sorted(request.template_package_versions):
            checks.append(
                format_compatibility_check(
                    kind="template",
                    source_version=version,
                    target_version=request.target.template_schema,
                    translators=self.template_translators,
                )
            )

        # Until a plugin proves a reversible state transition, migration of plugin-owned state is
        # treated as restore-required across releases. This prevents a code downgrade from being
        # mistaken for a safe rollback after the plugin store has moved forward.
        backup_required = bool(request.plugin_state_migration_required) or any(
            step.backup_required for step in steps
        )
        checks.extend(
            _backup_checks(
                request.backup_dir,
                current=request.current,
                required=backup_required,
                verifier=self.backup_verifier,
            )
        )

        return PreflightReport(
            current=request.current,
            target=request.target,
            planned_revisions=tuple(step.revision for step in steps),
            checks=tuple(checks),
            backup_required=backup_required,
            maintenance_required=(
                request.current.platform_release != request.target.platform_release
                or bool(steps)
                or bool(request.plugin_state_migration_required)
            ),
        )


def _target_migration_revision_checks(
    current: VersionSnapshot,
    target: VersionSnapshot,
    steps: tuple[MigrationStep, ...],
) -> tuple[PreflightCheck, ...]:
    expected = steps[-1].revision if steps else current.migration_revision
    if target.migration_revision != expected:
        return (
            PreflightCheck(
                code="migration.revision.target_mismatch",
                severity=CheckSeverity.ERROR,
                message=(
                    f"target migration revision {target.migration_revision!r} does not match "
                    f"planned revision {expected!r}"
                ),
                details={
                    "expected_revision": expected,
                    "target_revision": target.migration_revision,
                },
            ),
        )
    return (
        PreflightCheck(
            code="migration.revision.target_consistent",
            severity=CheckSeverity.INFO,
            message=f"target migration revision {expected!r} matches the migration plan",
        ),
    )


def _current_migration_revision_checks(
    current: VersionSnapshot,
    history: JsonMigrationHistoryStore,
) -> tuple[PreflightCheck, ...]:
    if current.migration_revision == BASELINE_MIGRATION_REVISION:
        return ()
    record = history.get(current.migration_revision)
    if record is None or record.status is not MigrationStatus.APPLIED:
        return (
            PreflightCheck(
                code="migration.revision.current_unproven",
                severity=CheckSeverity.ERROR,
                message=(
                    f"active migration revision {current.migration_revision!r} is not backed by "
                    "an applied migration-history record"
                ),
                details={"current_revision": current.migration_revision},
            ),
        )
    return (
        PreflightCheck(
            code="migration.revision.current_proven",
            severity=CheckSeverity.INFO,
            message=(
                f"active migration revision {current.migration_revision!r} is recorded as applied"
            ),
        ),
    )


def _migration_precondition_checks(
    steps: tuple[MigrationStep, ...],
    history: JsonMigrationHistoryStore,
    data_dir: Path,
) -> tuple[PreflightCheck, ...]:
    """Evaluate read-only invariants before any new migration mutation starts.

    A step precondition is deliberately an upgrade-source invariant, not an assertion that
    depends on mutations from an earlier step in the same plan. Intermediate expectations
    belong in the earlier step's post-validation. Already-started/applied revisions are not
    re-preflighted during recovery.
    """

    checks: list[PreflightCheck] = []
    context = MigrationContext(data_dir=data_dir)
    for step in steps:
        existing = history.get(step.revision)
        if existing is not None or step.precondition is None:
            continue
        try:
            step.precondition(context)
        except Exception as exc:
            checks.append(
                PreflightCheck(
                    code="migration.precondition.failed",
                    severity=CheckSeverity.ERROR,
                    message=f"migration {step.revision} precondition failed: {exc}",
                    details={"revision": step.revision, "error": f"{type(exc).__name__}: {exc}"},
                )
            )
        else:
            checks.append(
                PreflightCheck(
                    code="migration.precondition.satisfied",
                    severity=CheckSeverity.INFO,
                    message=f"migration {step.revision} precondition is satisfied",
                    details={"revision": step.revision},
                )
            )
    return tuple(checks)


def _version_checks(
    current: VersionSnapshot,
    target: VersionSnapshot,
) -> tuple[PreflightCheck, ...]:
    checks: list[PreflightCheck] = []
    if current.api != target.api:
        checks.append(
            PreflightCheck(
                code="api.version.changed",
                severity=CheckSeverity.ERROR,
                message=(
                    f"mixed/direct API upgrade {current.api!r} -> {target.api!r} is not "
                    "declared supported by this release"
                ),
            )
        )
    if current.worker_protocol != target.worker_protocol:
        checks.append(
            PreflightCheck(
                code="worker.protocol.changed",
                severity=CheckSeverity.ERROR,
                message=(
                    f"worker protocol {current.worker_protocol!r} -> {target.worker_protocol!r} "
                    "requires an explicit mixed-version/drain policy"
                ),
            )
        )
    if current.message_protocol != target.message_protocol:
        checks.append(
            PreflightCheck(
                code="message.protocol.changed",
                severity=CheckSeverity.ERROR,
                message=(
                    f"message protocol {current.message_protocol!r} -> {target.message_protocol!r} "
                    "requires an explicit compatibility policy"
                ),
            )
        )
    if current.backup_format != target.backup_format:
        checks.append(
            PreflightCheck(
                code="backup.format.changed",
                severity=CheckSeverity.WARNING,
                message=(
                    f"backup format changes from {current.backup_format!r} to "
                    f"{target.backup_format!r}; source-release backup remains the recovery artifact"
                ),
            )
        )
    return tuple(checks)


def _plugin_state_migration_checks(request: PreflightRequest) -> tuple[PreflightCheck, ...]:
    required = request.plugin_state_migration_required
    if not required:
        return ()
    known = {manifest.plugin_id for manifest in request.plugins}
    unknown = sorted(required - known)
    if unknown:
        return (
            PreflightCheck(
                code="plugin.state_migration.unknown",
                severity=CheckSeverity.ERROR,
                message="plugin state migration was requested for unknown plugin manifests",
                details={"plugin_ids": unknown},
            ),
        )
    if not request.plugin_state_migration_hook_available:
        return (
            PreflightCheck(
                code="plugin.state_migration.hook_missing",
                severity=CheckSeverity.ERROR,
                message=(
                    "plugin-owned state requires migration but no controlled #20 hook is available"
                ),
                details={"plugin_ids": sorted(required)},
            ),
        )
    return (
        PreflightCheck(
            code="plugin.state_migration.ready",
            severity=CheckSeverity.INFO,
            message="required plugin-owned state migrations have a controlled #20 hook",
            details={"plugin_ids": sorted(required)},
        ),
    )


def _storage_checks(data_dir: Path, minimum_free_bytes: int) -> tuple[PreflightCheck, ...]:
    root = data_dir.expanduser().resolve()
    checks: list[PreflightCheck] = []
    if not root.is_dir():
        return (
            PreflightCheck(
                code="storage.data_dir.missing",
                severity=CheckSeverity.ERROR,
                message=f"data directory does not exist: {root}",
            ),
        )
    if not os.access(root, os.R_OK | os.W_OK | os.X_OK):
        checks.append(
            PreflightCheck(
                code="storage.data_dir.permissions",
                severity=CheckSeverity.ERROR,
                message=f"data directory is not readable/writable: {root}",
            )
        )
    try:
        free = shutil.disk_usage(root).free
        checks.append(
            PreflightCheck(
                code="storage.disk.free",
                severity=(CheckSeverity.ERROR if free < minimum_free_bytes else CheckSeverity.INFO),
                message=f"{free} bytes free in data filesystem",
                details={"free_bytes": free, "minimum_free_bytes": minimum_free_bytes},
            )
        )
    except OSError as exc:
        checks.append(
            PreflightCheck(
                code="storage.disk.unavailable",
                severity=CheckSeverity.ERROR,
                message=f"cannot inspect filesystem capacity: {exc}",
            )
        )

    db_root = root / "db"
    if db_root.is_dir():
        seen: set[Path] = set()
        for pattern in ("*.sqlite", "*.sqlite3", "*.db"):
            for path in db_root.rglob(pattern):
                if path in seen:
                    continue
                seen.add(path)
                try:
                    uri = f"file:{path.resolve().as_posix()}?mode=ro"
                    with sqlite3.connect(uri, uri=True) as connection:
                        result = connection.execute("PRAGMA quick_check").fetchone()
                    if result is None or result[0] != "ok":
                        raise sqlite3.DatabaseError(f"quick_check returned {result!r}")
                except (OSError, sqlite3.DatabaseError) as exc:
                    checks.append(
                        PreflightCheck(
                            code="storage.sqlite.unhealthy",
                            severity=CheckSeverity.ERROR,
                            message=f"SQLite health check failed for {path.name}: {exc}",
                        )
                    )
    return tuple(checks)


def _backup_checks(
    backup_dir: Path | None,
    *,
    current: VersionSnapshot,
    required: bool,
    verifier: BackupVerifier,
) -> tuple[PreflightCheck, ...]:
    if backup_dir is None:
        return (
            PreflightCheck(
                code="backup.required.missing" if required else "backup.not_supplied",
                severity=CheckSeverity.ERROR if required else CheckSeverity.WARNING,
                message=(
                    "a verified source-release backup is required for this forward-only/risky upgrade"
                    if required
                    else "no backup supplied; recommended before upgrade"
                ),
            ),
        )
    try:
        verification = verifier(backup_dir)
    except (BackupError, OSError, ValueError) as exc:
        return (
            PreflightCheck(
                code="backup.invalid",
                severity=CheckSeverity.ERROR,
                message=f"backup verification failed: {exc}",
            ),
        )
    platform = verification.manifest.get("platform")
    source_version = platform.get("version") if isinstance(platform, dict) else None
    if source_version != current.platform_release:
        return (
            PreflightCheck(
                code="backup.source_version.mismatch",
                severity=CheckSeverity.ERROR,
                message=(
                    f"backup source platform {source_version!r} does not match installed "
                    f"platform {current.platform_release!r}"
                ),
            ),
        )
    return (
        PreflightCheck(
            code="backup.verified",
            severity=CheckSeverity.INFO,
            message="source-release backup is valid and matches the installed platform",
            details={"backup_dir": str(backup_dir)},
        ),
    )


def _configuration_checks(
    versions: Mapping[str, tuple[str, str]],
) -> tuple[PreflightCheck, ...]:
    checks: list[PreflightCheck] = []
    for name, (current, target) in sorted(versions.items()):
        checks.append(
            PreflightCheck(
                code="config.schema.compatible" if current == target else "config.schema.changed",
                severity=CheckSeverity.INFO if current == target else CheckSeverity.ERROR,
                message=(
                    f"configuration {name} schema remains {current}"
                    if current == target
                    else (
                        f"configuration {name} schema {current} -> {target} "
                        "needs an explicit translator"
                    )
                ),
            )
        )
    return tuple(checks)


def _historical_event_checks(versions: frozenset[str]) -> tuple[PreflightCheck, ...]:
    unsupported = sorted(versions - SUPPORTED_HISTORICAL_EVENT_SCHEMA_VERSIONS)
    if unsupported:
        return (
            PreflightCheck(
                code="history.event_schema.unsupported",
                severity=CheckSeverity.ERROR,
                message="historical event payloads contain unsupported schema versions",
                details={"unsupported_versions": unsupported},
            ),
        )
    if not versions:
        return ()
    return (
        PreflightCheck(
            code="history.event_schema.supported",
            severity=CheckSeverity.INFO,
            message="historical event schema versions remain interpretable",
            details={"versions": sorted(versions)},
        ),
    )