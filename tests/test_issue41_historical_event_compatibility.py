from __future__ import annotations

from pathlib import Path

from ai_multi_agent_platform.upgrade import (
    SUPPORTED_HISTORICAL_EVENT_SCHEMA_VERSIONS,
    FormatTranslatorRegistry,
    JsonMigrationHistoryStore,
    MigrationRegistry,
    PreflightRequest,
    UpgradePreflight,
    current_release_versions,
)


def test_supported_historical_event_versions_remain_interpretable(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    current = current_release_versions()
    preflight = UpgradePreflight(
        MigrationRegistry(),
        JsonMigrationHistoryStore.for_data_dir(data_dir),
        portable_translators=FormatTranslatorRegistry(current.portable_format),
        template_translators=FormatTranslatorRegistry(current.template_schema),
    )

    report = preflight.run(
        PreflightRequest(
            data_dir=data_dir,
            current=current,
            target=current,
            historical_event_schema_versions=SUPPORTED_HISTORICAL_EVENT_SCHEMA_VERSIONS,
        )
    )

    assert report.ok
    check = next(
        item for item in report.checks if item.code == "history.event_schema.supported"
    )
    assert check.details["versions"] == sorted(SUPPORTED_HISTORICAL_EVENT_SCHEMA_VERSIONS)
