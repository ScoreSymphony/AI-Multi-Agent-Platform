"""Schema evolution for persisted frontend customization payloads."""

from __future__ import annotations

from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

FRONTEND_CUSTOMIZATION_SCHEMA_VERSION = 2
SUPPORTED_FRONTEND_CUSTOMIZATION_SCHEMA_VERSIONS = (1, FRONTEND_CUSTOMIZATION_SCHEMA_VERSION)

_DEFAULT_WIDGET_SIZES: dict[str, JsonValue] = {
    "metrics": "wide",
    "recent-tasks": "normal",
    "recent-runs": "normal",
}


def migrate_frontend_customization(
    customization: dict[str, JsonValue],
    *,
    schema_version: int | None = None,
) -> dict[str, JsonValue]:
    """Return a current-schema copy without mutating caller-owned JSON data."""

    detected = customization.get("version")
    if isinstance(detected, bool) or not isinstance(detected, int):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "frontend customization version must be an integer",
        )
    if schema_version is not None and detected != schema_version:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "customization version does not match schema_version",
        )
    if detected not in SUPPORTED_FRONTEND_CUSTOMIZATION_SCHEMA_VERSIONS:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "unsupported frontend customization schema version",
            details={"schema_version": detected},
        )

    if detected == FRONTEND_CUSTOMIZATION_SCHEMA_VERSION:
        return dict(customization)

    # v1 -> v2 adds a persisted startup route and deterministic dashboard sizing.
    migrated = dict(customization)
    migrated["version"] = FRONTEND_CUSTOMIZATION_SCHEMA_VERSION

    raw_layout = migrated.get("layout")
    layout = dict(cast(dict[str, JsonValue], raw_layout)) if isinstance(raw_layout, dict) else {}
    layout.setdefault("startPage", "/")
    migrated["layout"] = cast(JsonValue, layout)

    raw_dashboard = migrated.get("dashboard")
    dashboard = (
        dict(cast(dict[str, JsonValue], raw_dashboard)) if isinstance(raw_dashboard, dict) else {}
    )
    raw_sizes = dashboard.get("widgetSizes")
    sizes = dict(cast(dict[str, JsonValue], raw_sizes)) if isinstance(raw_sizes, dict) else {}
    for widget, size in _DEFAULT_WIDGET_SIZES.items():
        sizes.setdefault(widget, size)
    dashboard["widgetSizes"] = cast(JsonValue, sizes)
    migrated["dashboard"] = cast(JsonValue, dashboard)

    return migrated


__all__ = [
    "FRONTEND_CUSTOMIZATION_SCHEMA_VERSION",
    "SUPPORTED_FRONTEND_CUSTOMIZATION_SCHEMA_VERSIONS",
    "migrate_frontend_customization",
]
