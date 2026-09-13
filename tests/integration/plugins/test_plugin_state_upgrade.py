from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.backup import BackupVerification
from ai_multi_agent_platform.plugins.models import PluginManifest, PluginProvenance
from ai_multi_agent_platform.upgrade import (
    FormatTranslatorRegistry,
    JsonMigrationHistoryStore,
    JsonUpgradeHistoryStore,
    JsonVersionStateStore,
    MaintenanceStateStore,
    MigrationRegistry,
    MigrationRunner,
    PreflightRequest,
    RollbackMode,
    UpgradeError,
    UpgradePreflight,
    UpgradeService,
    current_release_versions,
)


def _manifest(plugin_id: str = "fixture.plugin") -> PluginManifest:
    return PluginManifest(
        plugin_id=plugin_id,
        name="Fixture Plugin",
        description="Plugin state upgrade fixture",
        plugin_version="2.0",
        author="test",
        provenance=PluginProvenance(source="fixture", license="MIT"),
        extensions=(),
        state_version="2.0",
    )


def _service(
    data_dir: Path,
    *,
    current_platform: str,
    hook=None,
) -> UpgradeService:
    registry = MigrationRegistry()
    history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    versions = current_release_versions()
    portable = FormatTranslatorRegistry(versions.portable_format)
    template = FormatTranslatorRegistry(versions.template_schema)

    def verifier(path: Path) -> BackupVerification:
        return BackupVerification(
            backup_dir=path,
            files_checked=1,
            bytes_checked=1,
            manifest={"platform": {"version": current_platform}},
        )

    preflight = UpgradePreflight(
        registry,
        history,
        portable_translators=portable,
        template_translators=template,
        backup_verifier=verifier,
    )
    return UpgradeService(
        migrations=registry,
        runner=MigrationRunner(history),
        preflight=preflight,
        version_state=JsonVersionStateStore.for_data_dir(data_dir),
        maintenance=MaintenanceStateStore.for_data_dir(data_dir),
        history=JsonUpgradeHistoryStore.for_data_dir(data_dir),
        plugin_state_migration_hook=hook,
    )


def test_plugin_state_upgrade_requires_backup_and_controlled_hook(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    current = current_release_versions()
    target = replace(current, platform_release="0.0.2")
    manifest = _manifest()
    JsonVersionStateStore.for_data_dir(data_dir).initialize(current)
    request = PreflightRequest(
        data_dir=data_dir,
        current=current,
        target=target,
        backup_dir=tmp_path / "backup",
        plugins=(manifest,),
        plugin_state_migration_required=frozenset({manifest.plugin_id}),
    )

    service = _service(data_dir, current_platform=current.platform_release)

    with pytest.raises(UpgradeError, match="no controlled #20 hook"):
        service.apply(request, quiesced=True)

    assert JsonVersionStateStore.for_data_dir(data_dir).read() == current
    assert not MaintenanceStateStore.for_data_dir(data_dir).active()


def test_plugin_state_upgrade_runs_before_target_activation(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    current = current_release_versions()
    target = replace(current, platform_release="0.0.2")
    manifest = _manifest()
    state = JsonVersionStateStore.for_data_dir(data_dir)
    state.initialize(current)
    called: list[str] = []

    def hook(manifests: tuple[PluginManifest, ...]) -> None:
        assert state.read() == current
        called.extend(item.plugin_id for item in manifests)

    service = _service(
        data_dir,
        current_platform=current.platform_release,
        hook=hook,
    )
    result = service.apply(
        PreflightRequest(
            data_dir=data_dir,
            current=current,
            target=target,
            backup_dir=tmp_path / "backup",
            plugins=(manifest,),
            plugin_state_migration_required=frozenset({manifest.plugin_id}),
        ),
        quiesced=True,
    )

    assert called == [manifest.plugin_id]
    assert state.read() == target
    assert result.rollback_mode is RollbackMode.RESTORE_REQUIRED
    assert not MaintenanceStateStore.for_data_dir(data_dir).active()


def test_plugin_state_failure_remains_fail_closed_and_resumes_same_set(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    current = current_release_versions()
    target = replace(current, platform_release="0.0.2")
    manifest = _manifest()
    state = JsonVersionStateStore.for_data_dir(data_dir)
    state.initialize(current)
    request = PreflightRequest(
        data_dir=data_dir,
        current=current,
        target=target,
        backup_dir=tmp_path / "backup",
        plugins=(manifest,),
        plugin_state_migration_required=frozenset({manifest.plugin_id}),
    )

    def failing_hook(manifests: tuple[PluginManifest, ...]) -> None:
        raise RuntimeError("plugin fixture failed")

    failing = _service(
        data_dir,
        current_platform=current.platform_release,
        hook=failing_hook,
    )
    with pytest.raises(UpgradeError, match="plugin fixture failed"):
        failing.apply(request, quiesced=True)

    maintenance = MaintenanceStateStore.for_data_dir(data_dir)
    marker = maintenance.read()
    assert marker is not None
    assert marker.plugin_state_migrations == (manifest.plugin_id,)
    assert state.read() == current

    resumed_calls: list[str] = []

    def resumed_hook(manifests: tuple[PluginManifest, ...]) -> None:
        resumed_calls.extend(item.plugin_id for item in manifests)

    resumed = _service(
        data_dir,
        current_platform=current.platform_release,
        hook=resumed_hook,
    )
    result = resumed.apply(request, quiesced=True, resume_failed=True)

    assert resumed_calls == [manifest.plugin_id]
    assert result.rollback_mode is RollbackMode.RESTORE_REQUIRED
    assert state.read() == target
    assert not maintenance.active()


def test_plugin_state_resume_rejects_different_recorded_set(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    current = current_release_versions()
    target = replace(current, platform_release="0.0.2")
    first = _manifest("fixture.first")
    second = _manifest("fixture.second")
    state = JsonVersionStateStore.for_data_dir(data_dir)
    state.initialize(current)
    request = PreflightRequest(
        data_dir=data_dir,
        current=current,
        target=target,
        backup_dir=tmp_path / "backup",
        plugins=(first, second),
        plugin_state_migration_required=frozenset({first.plugin_id}),
    )

    def failing_hook(manifests: tuple[PluginManifest, ...]) -> None:
        raise RuntimeError("stop after maintenance marker")

    service = _service(
        data_dir,
        current_platform=current.platform_release,
        hook=failing_hook,
    )
    with pytest.raises(UpgradeError):
        service.apply(request, quiesced=True)

    different = replace(
        request,
        plugin_state_migration_required=frozenset({second.plugin_id}),
    )
    resumed = _service(
        data_dir,
        current_platform=current.platform_release,
        hook=lambda manifests: None,
    )
    with pytest.raises(UpgradeError, match="same plugin state migration set"):
        resumed.apply(different, quiesced=True, resume_failed=True)

    assert state.read() == current
    assert MaintenanceStateStore.for_data_dir(data_dir).active()
