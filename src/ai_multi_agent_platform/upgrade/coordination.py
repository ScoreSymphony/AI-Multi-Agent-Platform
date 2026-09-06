"""#41 integration for the platform-owned durable coordinator store."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.coordination.migrations import (
    COORDINATOR_MIGRATION_REVISION,
    COORDINATOR_SCHEMA_VERSION,
    CoordinatorMigrationError,
    coordinator_migration_plan,
    inspect_coordinator_store,
    migrate_coordinator_store,
)

from .migrations import MigrationContext, MigrationError, MigrationRunner, MigrationStep
from .models import CheckSeverity, PreflightCheck, PreflightReport
from .preflight import PreflightRequest, UpgradePreflight


class CoordinatorAwareUpgradePreflight(UpgradePreflight):
    """Extend the general #41 preflight with coordinator store migration evidence."""

    def run(self, request: PreflightRequest) -> PreflightReport:
        report = super().run(request)
        store = request.data_dir / "db" / "coordination.sqlite3"
        checks = list(report.checks)
        migration_required = False
        try:
            metadata = inspect_coordinator_store(store)
            plan = coordinator_migration_plan(store)
        except CoordinatorMigrationError as exc:
            checks.append(
                PreflightCheck(
                    code="coordination.migration.unsupported",
                    severity=CheckSeverity.ERROR,
                    message=str(exc),
                )
            )
        else:
            migration_required = bool(plan)
            if metadata is None:
                checks.append(
                    PreflightCheck(
                        code="coordination.store.uninitialized",
                        severity=CheckSeverity.INFO,
                        message=(
                            "coordinator store is absent/uninitialized and will use the current "
                            "schema when first materialized"
                        ),
                    )
                )
            elif migration_required:
                checks.append(
                    PreflightCheck(
                        code="coordination.migration.required",
                        severity=CheckSeverity.INFO,
                        message=(
                            f"coordinator store schema {metadata.schema_version} requires "
                            f"{COORDINATOR_MIGRATION_REVISION}"
                        ),
                        details={
                            "from_schema": metadata.schema_version,
                            "to_schema": COORDINATOR_SCHEMA_VERSION,
                            "revision": COORDINATOR_MIGRATION_REVISION,
                        },
                    )
                )
                if request.backup_dir is None:
                    checks.append(
                        PreflightCheck(
                            code="coordination.migration.backup_required",
                            severity=CheckSeverity.ERROR,
                            message=(
                                "coordinator store migration requires a verified source-release "
                                "backup before mutation"
                            ),
                        )
                    )
            else:
                checks.append(
                    PreflightCheck(
                        code="coordination.migration.current",
                        severity=CheckSeverity.INFO,
                        message=(
                            f"coordinator store is current at schema {COORDINATOR_SCHEMA_VERSION} "
                            f"/ {COORDINATOR_MIGRATION_REVISION}"
                        ),
                    )
                )

        return replace(
            report,
            checks=tuple(checks),
            backup_required=report.backup_required or migration_required,
            maintenance_required=report.maintenance_required or migration_required,
        )


class CoordinatorAwareMigrationRunner(MigrationRunner):
    """Run generic #41 migrations, then the store-local coordinator migration."""

    def apply(
        self,
        steps: tuple[MigrationStep, ...],
        context: MigrationContext,
        *,
        resume_failed: bool = False,
    ) -> tuple[str, ...]:
        applied = super().apply(steps, context, resume_failed=resume_failed)
        try:
            migrate_coordinator_store(context.data_dir / "db" / "coordination.sqlite3")
        except CoordinatorMigrationError as exc:
            raise MigrationError(f"coordinator store migration failed: {exc}") from exc
        return applied
