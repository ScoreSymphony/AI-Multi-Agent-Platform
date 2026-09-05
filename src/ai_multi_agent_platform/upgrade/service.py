"""Coordinated, fail-closed upgrade application service for issue #41."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .migrations import MigrationContext, MigrationRegistry, MigrationRunner
from .models import RollbackMode, UpgradeResult
from .preflight import PreflightRequest, UpgradePreflight
from .versioning import JsonVersionStateStore


class UpgradeError(RuntimeError):
    """Raised when an upgrade cannot safely proceed."""


@dataclass(frozen=True, slots=True)
class MaintenanceState:
    started_at: str
    source_release: str
    target_release: str
    planned_revisions: tuple[str, ...]


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

    def enter(self, state: MaintenanceState, *, resume_existing: bool = False) -> None:
        if self.path.exists() and not resume_existing:
            raise UpgradeError(
                "upgrade maintenance marker already exists; recover or explicitly resume first"
            )
        document = {
            "schema_version": self.SCHEMA_VERSION,
            "started_at": state.started_at,
            "source_release": state.source_release,
            "target_release": state.target_release,
            "planned_revisions": list(state.planned_revisions),
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
        entries: list[object] = []
        if self.path.exists():
            raw: object = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("schema_version") != self.SCHEMA_VERSION:
                raise UpgradeError("unsupported upgrade-history document")
            persisted = raw.get("upgrades")
            if not isinstance(persisted, list):
                raise UpgradeError("upgrade history must contain an upgrades array")
            entries = list(persisted)
        entries.append(result.to_dict())
        document = {"schema_version": self.SCHEMA_VERSION, "upgrades": entries}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


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
    ) -> None:
        self.migrations = migrations
        self.runner = runner
        self.preflight = preflight
        self.version_state = version_state
        self.maintenance = maintenance
        self.history = history

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

        report = self.preflight.run(request)
        if not report.ok:
            failures = "; ".join(
                check.message for check in report.checks if check.severity.value == "error"
            )
            raise UpgradeError(f"upgrade preflight failed: {failures}")
        if report.maintenance_required and not quiesced:
            raise UpgradeError("migration upgrade requires explicitly quiesced/drained work")
        if self.maintenance.active() and not resume_failed:
            raise UpgradeError("deployment is already in upgrade maintenance mode")

        steps = self.migrations.plan(request.current.domain_schema, request.target.domain_schema)
        started_at = _now()
        if report.maintenance_required:
            self.maintenance.enter(
                MaintenanceState(
                    started_at=started_at,
                    source_release=request.current.platform_release,
                    target_release=request.target.platform_release,
                    planned_revisions=report.planned_revisions,
                ),
                resume_existing=resume_failed,
            )

        migration_context = context or MigrationContext(data_dir=request.data_dir)
        applied = self.runner.apply(
            steps,
            migration_context,
            resume_failed=resume_failed,
        )

        # Version-state activation is intentionally after all migration validation. A failed
        # migration therefore cannot advertise the target release/schema as active.
        self.version_state.write(request.target)
        result = UpgradeResult(
            started_at=started_at,
            finished_at=_now(),
            previous=request.current,
            current=request.target,
            applied_revisions=applied,
            backup_dir=str(request.backup_dir) if request.backup_dir is not None else None,
            rollback_mode=_rollback_mode(steps),
        )
        self.history.append(result)
        self.maintenance.clear()
        return result


def _rollback_mode(steps: tuple[object, ...]) -> RollbackMode:
    modes = [getattr(step, "rollback_mode", RollbackMode.CODE_ONLY_BEFORE_MIGRATION) for step in steps]
    if any(mode is RollbackMode.RESTORE_REQUIRED for mode in modes):
        return RollbackMode.RESTORE_REQUIRED
    if modes and all(mode is RollbackMode.REVERSIBLE for mode in modes):
        return RollbackMode.REVERSIBLE
    return RollbackMode.CODE_ONLY_BEFORE_MIGRATION


def _now() -> str:
    return datetime.now(UTC).isoformat()
