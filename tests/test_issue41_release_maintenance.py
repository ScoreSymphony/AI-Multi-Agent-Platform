from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.upgrade import (
    FormatTranslatorRegistry,
    JsonMigrationHistoryStore,
    JsonUpgradeHistoryStore,
    JsonVersionStateStore,
    MaintenanceStateStore,
    MigrationRegistry,
    MigrationRunner,
    PreflightRequest,
    UpgradeError,
    UpgradePreflight,
    UpgradeService,
    current_release_versions,
)


def test_release_only_upgrade_requires_quiesced_maintenance(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    target = current_release_versions()
    current = replace(target, platform_release="0.0.0")
    registry = MigrationRegistry()
    migration_history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    preflight = UpgradePreflight(
        registry,
        migration_history,
        portable_translators=FormatTranslatorRegistry(target.portable_format),
        template_translators=FormatTranslatorRegistry(target.template_schema),
    )
    request = PreflightRequest(data_dir=data_dir, current=current, target=target)
    report = preflight.run(request)

    assert report.ok
    assert report.planned_revisions == ()
    assert report.maintenance_required

    version_state = JsonVersionStateStore.for_data_dir(data_dir)
    version_state.initialize(current)
    maintenance = MaintenanceStateStore.for_data_dir(data_dir)
    service = UpgradeService(
        migrations=registry,
        runner=MigrationRunner(migration_history),
        preflight=preflight,
        version_state=version_state,
        maintenance=maintenance,
        history=JsonUpgradeHistoryStore.for_data_dir(data_dir),
    )

    with pytest.raises(UpgradeError, match="quiesced"):
        service.apply(request, quiesced=False)

    assert version_state.read() == current
    assert not maintenance.active()

    result = service.apply(request, quiesced=True)

    assert result.previous == current
    assert result.current == target
    assert result.applied_revisions == ()
    assert version_state.read() == target
    assert not maintenance.active()
