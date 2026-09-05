"""Coordinated, fail-closed upgrade application service for issue #41."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from ai_multi_agent_platform.plugins.models import PluginManifest

from .migrations import MigrationContext, MigrationRegistry, MigrationRunner, MigrationStep
from .models import CheckSeverity, MigrationStatus, RollbackMode, UpgradeResult, VersionSnapshot
from .preflight import PreflightRequest, UpgradePreflight
from .versioning import JsonVersionStateStore, version_snapshot_from_dict

PluginStateMigrationHook = Callable[[tuple[PluginManifest, ...]], None]


class UpgradeError(RuntimeError):
    """Raised when an upgrade cannot safely proceed."""


@dataclass(frozen=True, slots=True)
class MaintenanceState:
    started_at: str
    source: VersionSnapshot
    target: VersionSnapshot
    planned_revisions: tuple[str, ...]
    plugin_state_migrations: tuple[str, ...] = ()
    backup_dir: str | None = None


class MaintenanceStateStore:
    """Durable marker preventing failed/interrupted upgrades from silently resuming work."""

    SCHEMA_VERSION = "1"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def for_data_dir(cls, data_dir: str | Path) -> MaintenanceStateStore:
        return cls(Path(data_dir) / "db" / "upgrade-maintenance.json")

    def active(self) -> bool:
        return self.path.is_file()

    def read(self) -> MaintenanceState | None:
        if not self.path.is_file():
            return None
        try:
            raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UpgradeError(f"cannot read upgrade maintenance marker: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != self.SCHEMA_VERSION:
            raise UpgradeError("unsupported upgrade maintenance marker")
        started_at = raw.get("started_at")
        source = raw.get("source_versions")
        target = raw.get("target_versions")
        planned = raw.get("planned_revisions")
        plugin_state_migrations = raw.get("plugin_state_migrations", [])
        backup_dir = raw.get("backup_dir")
        if not isinstance(started_at, str) or not started_at:
            raise UpgradeError("upgrade maintenance marker has invalid started_at")
        if not isinstance(source, dict) or not isinstance(target, dict):
            raise UpgradeError(
                "upgrade maintenance marker is missing source/target version vectors"
            )
        if not isinstance(planned, list) or any(
            not isinstance(item, str) or not item for item in planned
        ):
            raise UpgradeError("upgrade maintenance marker has invalid planned revisions")
        if not isinstance(plugin_state_migrations, list) or any(
            not isinstance(item, str) or not item for item in plugin_state_migrations
        ):
            raise UpgradeError("upgrade maintenance marker has invalid plugin state migrations")
        if backup_dir is not None and (not isinstance(backup_dir, str) or not backup_dir):
            raise UpgradeError("upgrade maintenance marker has invalid backup directory")
        return MaintenanceState(
            started_at=started_at,
            source=version_snapshot_from_dict(source),
            target=version_snapshot_from_dict(target),
            planned_revisions=tuple(planned),
            plugin_state_migrations=tuple(plugin_state_migrations),
            backup_dir=backup_dir,
        )

    def enter(self, state: MaintenanceState, *, resume_existing: bool = False) -> None:
        existing = self.read()
        if existing is not None:
            if not resume_existing:
                raise UpgradeError(
                    "upgrade maintenance marker already exists; recover or explicitly resume first"
                )
            if existing != state:
                raise UpgradeError(
                    "existing upgrade maintenance marker belongs to a different upgrade attempt"
                )
            return
        document = {
            "schema_version": self.SCHEMA_VERSION,
            "started_at": state.started_at,
            "source_release": state.source.platform_release,
            "target_release": state.target.platform_release,
            "source_versions": state.source.to_dict(),
            "target_versions": state.target.to_dict(),
            "planned_revisions": list(state.planned_revisions),
            "plugin_state_migrations": list(state.plugin_state_migrations),
            "backup_dir": state.backup_dir,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


class JsonUpgradeHistoryStore:
    SCHEMA_VERSION = "1"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def for_data_dir(cls, data_dir: str | Path) -> JsonUpgradeHistoryStore:
        return cls(Path(data_dir) / "db" / "upgrade-history.json")

    def append(self, result: UpgradeResult) -> None:
        entries = self._entries()
        for existing in entries:
            if not isinstance(existing, dict) or existing.get("started_at") != result.started_at:
                continue
            if (
                existing.get("previous") != result.previous.to_dict()
                or existing.get("current") != result.current.to_dict()
            ):
                raise UpgradeError(
                    "upgrade history contains a conflicting result for this upgrade attempt"
                )
            return
        entries.append(result.to_dict())
        document = {"schema_version": self.SCHEMA_VERSION, "upgrades": entries}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _entries(self) -> list[object]:
        if not self.path.exists():
            return []
        try:
            raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UpgradeError(f"cannot read upgrade history: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != self.SCHEMA_VERSION:
            raise UpgradeError("unsupported upgrade-history document")
        persisted = raw.get("upgrades")
        if not isinstance(persisted, list):
            raise UpgradeError("upgrade history must contain an upgrades array")
        return list(persisted)


class UpgradeService:
    def __init__(
        self,
        *,
        migrations: MigrationRegistry,
        runner: MigrationRunner,
        preflight: UpgradePreflight,
        version_state: JsonVersionStateStore,
        maintenance: MaintenanceStateStore,
        history: JsonUpgradeHistoryStore,
        plugin_state_migration_hook: PluginStateMigrationHook | None = None,
    ) -> None:
        self.migrations = migrations
        self.runner = runner
        self.preflight = preflight
        self.version_state = version_state
        self.maintenance = maintenance
        self.history = history
        self.plugin_state_migration_hook = plugin_state_migration_hook

    def apply(
        self,
        request: PreflightRequest,
        *,
        quiesced: bool,
        resume_failed: bool = False,
        context: MigrationContext | None = None,
    ) -> UpgradeResult:
        """Apply only after a fresh successful preflight.

        The caller/deployment composition owns actual worker draining and task-dispatch pause.
        `quiesced=True` is the explicit proof supplied by that operator boundary. The durable
        maintenance marker remains present after an interrupted/failed migration so startup or
        operator tooling cannot mistake a partially migrated deployment for a healthy one.
        """

        installed = self.version_state.read()
        maintenance_state = self.maintenance.read()
        effective_request = replace(
            request,
            resume_failed=resume_failed,
            plugin_state_migration_hook_available=self.plugin_state_migration_hook is not None,
        )

        if maintenance_state is not None:
            if not resume_failed:
                raise UpgradeError("deployment is already in upgrade maintenance mode")
            if installed == maintenance_state.target:
                return self._finalize_interrupted_activation(
                    maintenance_state,
                    effective_request,
                    quiesced=quiesced,
                )
            if installed != maintenance_state.source:
                raise UpgradeError(
                    "installed version state matches neither source nor target of the active upgrade"
                )
            if (
                request.current != maintenance_state.source
                or request.target != maintenance_state.target
            ):
                raise UpgradeError(
                    "active maintenance marker belongs to a different source/target upgrade"
                )
            effective_request = self._resume_request(effective_request, maintenance_state)
        elif installed != request.current:
            raise UpgradeError(
                "installed version state changed since the upgrade request was prepared; rerun preflight"
            )

        report = self.preflight.run(effective_request)
        if not report.ok:
            failures = "; ".join(
                check.message for check in report.checks if check.severity is CheckSeverity.ERROR
            )
            raise UpgradeError(f"upgrade preflight failed: {failures}")
        if report.maintenance_required and not quiesced:
            raise UpgradeError("migration upgrade requires explicitly quiesced/drained work")

        steps = self.migrations.plan(
            effective_request.current.domain_schema,
            effective_request.target.domain_schema,
        )
        started_at = maintenance_state.started_at if maintenance_state is not None else _now()
        if report.maintenance_required and maintenance_state is None:
            self.maintenance.enter(
                MaintenanceState(
                    started_at=started_at,
                    source=effective_request.current,
                    target=effective_request.target,
                    planned_revisions=report.planned_revisions,
                    plugin_state_migrations=tuple(
                        sorted(effective_request.plugin_state_migration_required)
                    ),
                    backup_dir=(
                        str(effective_request.backup_dir)
                        if effective_request.backup_dir is not None
                        else None
                    ),
                )
            )

        migration_context = context or MigrationContext(data_dir=effective_request.data_dir)
        self.runner.apply(
            steps,
            migration_context,
            resume_failed=resume_failed,
        )
        self._migrate_plugin_state(effective_request)

        result = UpgradeResult(
            started_at=started_at,
            finished_at=_now(),
            previous=effective_request.current,
            current=effective_request.target,
            applied_revisions=report.planned_revisions,
            backup_dir=(
                str(effective_request.backup_dir)
                if effective_request.backup_dir is not None
                else None
            ),
            rollback_mode=_rollback_mode(
                steps,
                plugin_state_migration_required=bool(
                    effective_request.plugin_state_migration_required
                ),
            ),
        )

        # Persist the completed-attempt record before activating the target version vector. If
        # either final metadata write is interrupted, the maintenance marker keeps the deployment
        # fail-closed and a resume can deterministically finish activation without rerunning data
        # mutations or inventing a second upgrade attempt.
        self.history.append(result)
        self.version_state.write(effective_request.target)
        self.maintenance.clear()
        return result

    def _migrate_plugin_state(self, request: PreflightRequest) -> None:
        required = request.plugin_state_migration_required
        if not required:
            return
        hook = self.plugin_state_migration_hook
        if hook is None:
            raise UpgradeError("plugin state migration hook became unavailable after preflight")
        manifests = tuple(
            manifest for manifest in request.plugins if manifest.plugin_id in required
        )
        try:
            # The deployment wrapper must call the deterministic #20 PluginStateMigrator. That
            # migrator is version-aware and therefore safe to invoke again during explicit resume.
            hook(manifests)
        except Exception as exc:
            raise UpgradeError(f"plugin-owned state migration failed: {exc}") from exc

    def _resume_request(
        self,
        request: PreflightRequest,
        state: MaintenanceState,
    ) -> PreflightRequest:
        recorded_plugins = frozenset(state.plugin_state_migrations)
        if request.plugin_state_migration_required and (
            request.plugin_state_migration_required != recorded_plugins
        ):
            raise UpgradeError(
                "resume must use the same plugin state migration set recorded for the upgrade"
            )
        resumed = replace(
            request,
            plugin_state_migration_required=recorded_plugins,
            plugin_state_migration_hook_available=self.plugin_state_migration_hook is not None,
        )
        if state.backup_dir is None:
            return resumed
        recorded_backup = Path(state.backup_dir).expanduser().resolve()
        if request.backup_dir is None:
            return replace(resumed, backup_dir=recorded_backup)
        supplied_backup = request.backup_dir.expanduser().resolve()
        if supplied_backup != recorded_backup:
            raise UpgradeError(
                "resume must use the same verified backup recorded for the active upgrade attempt"
            )
        return resumed

    def _finalize_interrupted_activation(
        self,
        state: MaintenanceState,
        request: PreflightRequest,
        *,
        quiesced: bool,
    ) -> UpgradeResult:
        if not quiesced:
            raise UpgradeError(
                "finalizing an interrupted upgrade requires explicitly quiesced/drained work"
            )
        if request.target != state.target:
            raise UpgradeError("active maintenance marker targets a different platform release")
        if request.plugin_state_migration_required and (
            request.plugin_state_migration_required != frozenset(state.plugin_state_migrations)
        ):
            raise UpgradeError(
                "active maintenance marker has a different plugin state migration set"
            )
        steps = self.migrations.plan(state.source.domain_schema, state.target.domain_schema)
        revisions = tuple(step.revision for step in steps)
        if revisions != state.planned_revisions:
            raise UpgradeError(
                "active maintenance marker no longer matches the immutable migration registry"
            )
        records = {record.revision: record for record in self.runner.history.records()}
        incomplete = [
            revision
            for revision in revisions
            if revision not in records or records[revision].status is not MigrationStatus.APPLIED
        ]
        if incomplete:
            raise UpgradeError(
                "target version was activated before all planned migrations were recorded "
                "as applied: " + ", ".join(incomplete)
            )
        result = UpgradeResult(
            started_at=state.started_at,
            finished_at=_now(),
            previous=state.source,
            current=state.target,
            applied_revisions=state.planned_revisions,
            backup_dir=state.backup_dir,
            rollback_mode=_rollback_mode(
                steps,
                plugin_state_migration_required=bool(state.plugin_state_migrations),
            ),
        )
        self.history.append(result)
        self.maintenance.clear()
        return result


def _rollback_mode(
    steps: tuple[MigrationStep, ...],
    *,
    plugin_state_migration_required: bool = False,
) -> RollbackMode:
    if plugin_state_migration_required:
        return RollbackMode.RESTORE_REQUIRED
    modes = [step.rollback_mode for step in steps]
    if any(mode is RollbackMode.RESTORE_REQUIRED for mode in modes):
        return RollbackMode.RESTORE_REQUIRED
    if modes and all(mode is RollbackMode.REVERSIBLE for mode in modes):
        return RollbackMode.REVERSIBLE
    return RollbackMode.CODE_ONLY_BEFORE_MIGRATION


def _now() -> str:
    return datetime.now(UTC).isoformat()
