from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.upgrade import (
    FormatTranslatorRegistry,
    JsonMigrationHistoryStore,
    JsonUpgradeHistoryStore,
    JsonVersionStateStore,
    MaintenanceState,
    MaintenanceStateStore,
    MigrationContext,
    MigrationRegistry,
    MigrationRunner,
    MigrationStep,
    PreflightRequest,
    RollbackMode,
    UpgradeError,
    UpgradePreflight,
    UpgradeResult,
    UpgradeService,
    current_release_versions,
)


def _data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    (root / "db").mkdir(parents=True)
    return root


def _versions(*, platform: str, schema: str, revision: str):
    return replace(
        current_release_versions(migration_revision=revision),
        platform_release=platform,
        domain_schema=schema,
    )


def _service(
    data_dir: Path,
    registry: MigrationRegistry,
    migration_history: JsonMigrationHistoryStore,
    version_state: JsonVersionStateStore,
    maintenance: MaintenanceStateStore,
    upgrade_history: JsonUpgradeHistoryStore,
) -> UpgradeService:
    release = current_release_versions()
    preflight = UpgradePreflight(
        registry,
        migration_history,
        portable_translators=FormatTranslatorRegistry(release.portable_format),
        template_translators=FormatTranslatorRegistry(release.template_schema),
    )
    return UpgradeService(
        migrations=registry,
        runner=MigrationRunner(migration_history),
        preflight=preflight,
        version_state=version_state,
        maintenance=maintenance,
        history=upgrade_history,
    )


def test_resume_finalizes_target_activated_before_maintenance_cleanup(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    old = _versions(platform="0.0.0", schema="0.9", revision="baseline")
    target = _versions(platform="0.0.1", schema="1.0", revision="r001")
    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="activation recovery fixture",
        apply=lambda context: None,
        restart_safe=True,
        rollback_mode=RollbackMode.REVERSIBLE,
    )
    registry = MigrationRegistry((step,))
    migration_history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    version_state = JsonVersionStateStore.for_data_dir(data_dir)
    version_state.initialize(old)
    maintenance = MaintenanceStateStore.for_data_dir(data_dir)
    maintenance.enter(
        MaintenanceState(
            started_at="2026-09-05T20:00:00+00:00",
            source=old,
            target=target,
            planned_revisions=("r001",),
        )
    )
    MigrationRunner(migration_history).apply((step,), MigrationContext(data_dir=data_dir))

    # Simulate interruption after target activation but before upgrade-history/marker finalization.
    version_state.write(target)
    upgrade_history = JsonUpgradeHistoryStore.for_data_dir(data_dir)
    service = _service(
        data_dir,
        registry,
        migration_history,
        version_state,
        maintenance,
        upgrade_history,
    )

    result = service.apply(
        PreflightRequest(data_dir=data_dir, current=target, target=target),
        quiesced=True,
        resume_failed=True,
    )

    assert result.previous == old
    assert result.current == target
    assert result.applied_revisions == ("r001",)
    assert version_state.read() == target
    assert not maintenance.active()
    document = json.loads(upgrade_history.path.read_text(encoding="utf-8"))
    assert len(document["upgrades"]) == 1
    assert document["upgrades"][0]["previous"] == old.to_dict()
    assert document["upgrades"][0]["current"] == target.to_dict()


def test_resume_after_history_persisted_does_not_duplicate_upgrade_attempt(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    old = _versions(platform="0.0.0", schema="0.9", revision="baseline")
    target = _versions(platform="0.0.1", schema="1.0", revision="r001")
    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="history recovery fixture",
        apply=lambda context: None,
        restart_safe=True,
        rollback_mode=RollbackMode.REVERSIBLE,
    )
    registry = MigrationRegistry((step,))
    migration_history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    MigrationRunner(migration_history).apply((step,), MigrationContext(data_dir=data_dir))
    version_state = JsonVersionStateStore.for_data_dir(data_dir)
    version_state.initialize(old)
    maintenance = MaintenanceStateStore.for_data_dir(data_dir)
    started_at = "2026-09-05T20:01:00+00:00"
    maintenance.enter(
        MaintenanceState(
            started_at=started_at,
            source=old,
            target=target,
            planned_revisions=("r001",),
        )
    )
    upgrade_history = JsonUpgradeHistoryStore.for_data_dir(data_dir)
    upgrade_history.append(
        UpgradeResult(
            started_at=started_at,
            finished_at="2026-09-05T20:01:01+00:00",
            previous=old,
            current=target,
            applied_revisions=("r001",),
            backup_dir=None,
            rollback_mode=RollbackMode.REVERSIBLE,
        )
    )
    service = _service(
        data_dir,
        registry,
        migration_history,
        version_state,
        maintenance,
        upgrade_history,
    )

    result = service.apply(
        PreflightRequest(data_dir=data_dir, current=old, target=target),
        quiesced=True,
        resume_failed=True,
    )

    assert result.current == target
    assert version_state.read() == target
    assert not maintenance.active()
    document = json.loads(upgrade_history.path.read_text(encoding="utf-8"))
    assert len(document["upgrades"]) == 1


def test_resume_rejects_marker_for_different_target(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    old = _versions(platform="0.0.0", schema="0.9", revision="baseline")
    target = _versions(platform="0.0.1", schema="1.0", revision="r001")
    different_target = replace(target, platform_release="0.0.2")
    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="marker mismatch fixture",
        apply=lambda context: None,
        restart_safe=True,
    )
    registry = MigrationRegistry((step,))
    migration_history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    version_state = JsonVersionStateStore.for_data_dir(data_dir)
    version_state.initialize(old)
    maintenance = MaintenanceStateStore.for_data_dir(data_dir)
    maintenance.enter(
        MaintenanceState(
            started_at="2026-09-05T20:02:00+00:00",
            source=old,
            target=target,
            planned_revisions=("r001",),
        )
    )
    service = _service(
        data_dir,
        registry,
        migration_history,
        version_state,
        maintenance,
        JsonUpgradeHistoryStore.for_data_dir(data_dir),
    )

    with pytest.raises(UpgradeError, match="different source/target"):
        service.apply(
            PreflightRequest(data_dir=data_dir, current=old, target=different_target),
            quiesced=True,
            resume_failed=True,
        )

    assert version_state.read() == old
    assert maintenance.active()
