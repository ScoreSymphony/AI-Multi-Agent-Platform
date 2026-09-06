"""Validated platform upgrade and migration lifecycle."""

from .compatibility import (
    CompatibilityError,
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
    MigrationRunner,
    MigrationStep,
)
from .models import (
    CheckSeverity,
    MigrationRecord,
    MigrationStatus,
    PreflightCheck,
    PreflightReport,
    RollbackMode,
    UpgradeResult,
    VersionSnapshot,
)
from .preflight import (
    SUPPORTED_HISTORICAL_EVENT_SCHEMA_VERSIONS,
    PreflightRequest,
    UpgradePreflight,
)
from .service import (
    JsonUpgradeHistoryStore,
    MaintenanceState,
    MaintenanceStateStore,
    UpgradeError,
    UpgradeService,
)
from .versioning import (
    BASELINE_MIGRATION_REVISION,
    JsonVersionStateStore,
    VersionStateError,
    current_release_versions,
)

__all__ = [
    "BASELINE_MIGRATION_REVISION",
    "SUPPORTED_HISTORICAL_EVENT_SCHEMA_VERSIONS",
    "CheckSeverity",
    "CompatibilityError",
    "ExtensionCompatibilitySpec",
    "FormatTranslatorRegistry",
    "JsonMigrationHistoryStore",
    "JsonUpgradeHistoryStore",
    "JsonVersionStateStore",
    "MaintenanceState",
    "MaintenanceStateStore",
    "MigrationContext",
    "MigrationError",
    "MigrationRecord",
    "MigrationRegistry",
    "MigrationRunner",
    "MigrationStatus",
    "MigrationStep",
    "PreflightCheck",
    "PreflightReport",
    "PreflightRequest",
    "RollbackMode",
    "UpgradeError",
    "UpgradePreflight",
    "UpgradeResult",
    "UpgradeService",
    "VersionSnapshot",
    "VersionStateError",
    "current_release_versions",
    "extension_compatibility_checks",
    "format_compatibility_check",
    "plugin_compatibility_checks",
]
