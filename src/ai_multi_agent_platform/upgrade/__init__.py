"""Validated platform upgrade and migration lifecycle."""

from .compatibility import (
    CompatibilityError,
    ExtensionCompatibilitySpec,
    FormatTranslatorRegistry,
    extension_compatibility_checks,
    format_compatibility_check,
    plugin_compatibility_checks,
)
from .coordination import (
    CoordinatorAwareMigrationRunner,
    CoordinatorAwareUpgradePreflight,
)
from .migrations import (
    JsonMigrationHistoryStore,
    MigrationContext,
    MigrationError,
    MigrationRegistry,
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
from .preflight import SUPPORTED_HISTORICAL_EVENT_SCHEMA_VERSIONS, PreflightRequest
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

# The public #41 path includes all platform-owned durable stores. The base classes remain available
# from their implementation modules for focused unit tests and future provider-specific composition.
MigrationRunner = CoordinatorAwareMigrationRunner
UpgradePreflight = CoordinatorAwareUpgradePreflight

__all__ = [
    "BASELINE_MIGRATION_REVISION",
    "SUPPORTED_HISTORICAL_EVENT_SCHEMA_VERSIONS",
    "CheckSeverity",
    "CompatibilityError",
    "CoordinatorAwareMigrationRunner",
    "CoordinatorAwareUpgradePreflight",
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
