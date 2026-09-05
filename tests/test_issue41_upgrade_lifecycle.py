from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.backup import BackupVerification
from ai_multi_agent_platform.plugins.models import PluginManifest, PluginProvenance, VersionRange
from ai_multi_agent_platform.upgrade import (
    CheckSeverity,
    ExtensionCompatibilitySpec,
    FormatTranslatorRegistry,
    JsonMigrationHistoryStore,
    JsonUpgradeHistoryStore,
    JsonVersionStateStore,
    MaintenanceStateStore,
    MigrationContext,
    MigrationError,
    MigrationRegistry,
    MigrationRunner,
    MigrationStatus,
    MigrationStep,
    PreflightRequest,
    RollbackMode,
    UpgradeError,
    UpgradePreflight,
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


def _preflight(
    data_dir: Path,
    registry: MigrationRegistry,
    *,
    backup_verifier=None,
) -> UpgradePreflight:
    history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    current = current_release_versions()
    portable = FormatTranslatorRegistry(current.portable_format)
    template = FormatTranslatorRegistry(current.template_schema)
    kwargs = {}
    if backup_verifier is not None:
        kwargs["backup_verifier"] = backup_verifier
    return UpgradePreflight(
        registry,
        history,
        portable_translators=portable,
        template_translators=template,
        **kwargs,
    )


def test_version_dimensions_are_explicit_and_independent() -> None:
    versions = current_release_versions(
        migration_revision="r17",
        adapter_versions={"hermes": "abc123"},
        plugin_interface_versions={"tool": "2.0"},
    )

    assert versions.platform_release == "0.0.1"
    assert versions.domain_schema == "1.0"
    assert versions.api == "v1"
    assert versions.migration_revision == "r17"
    assert versions.plugin_manifest == "1"
    assert versions.portable_format == "1.0"
    assert versions.template_schema == "1"
    assert versions.backup_format == "1"
    assert versions.worker_protocol == "1.0"
    assert versions.message_protocol == "1.0"
    assert versions.adapter_versions == {"hermes": "abc123"}
    assert versions.plugin_interface_versions == {"tool": "2.0"}


def test_version_state_requires_explicit_baseline_adoption(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    store = JsonVersionStateStore.for_data_dir(data_dir)

    with pytest.raises(Exception, match="not initialized"):
        store.read()

    adopted = store.initialize()
    assert store.read() == adopted
    with pytest.raises(Exception, match="already initialized"):
        store.initialize()


def test_upgrade_from_previous_schema_fixture_records_history(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    old = _versions(platform="0.0.0", schema="0.9", revision="baseline")
    target = _versions(platform="0.0.1", schema="1.0", revision="r001")
    marker = data_dir / "db" / "fixture-v1.txt"

    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="fixture migration proving ordered schema evolution",
        apply=lambda context: marker.write_text("migrated\n", encoding="utf-8"),
        validate=lambda context: (
            marker.read_text(encoding="utf-8") == "migrated\n"
            or (_ for _ in ()).throw(ValueError("fixture validation failed"))
        ),
        transactional=False,
        restart_safe=True,
        rollback_mode=RollbackMode.REVERSIBLE,
    )
    registry = MigrationRegistry((step,))
    history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    state = JsonVersionStateStore.for_data_dir(data_dir)
    state.initialize(old)
    preflight = _preflight(data_dir, registry)
    service = UpgradeService(
        migrations=registry,
        runner=MigrationRunner(history),
        preflight=preflight,
        version_state=state,
        maintenance=MaintenanceStateStore.for_data_dir(data_dir),
        history=JsonUpgradeHistoryStore.for_data_dir(data_dir),
    )

    result = service.apply(
        PreflightRequest(data_dir=data_dir, current=old, target=target),
        quiesced=True,
    )

    assert marker.read_text(encoding="utf-8") == "migrated\n"
    assert result.applied_revisions == ("r001",)
    assert result.rollback_mode is RollbackMode.REVERSIBLE
    assert state.read() == target
    records = history.records()
    assert len(records) == 1
    assert records[0].status is MigrationStatus.APPLIED
    assert not MaintenanceStateStore.for_data_dir(data_dir).active()


def test_already_applied_migration_is_idempotently_skipped(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    calls: list[str] = []
    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="idempotency fixture",
        apply=lambda context: calls.append("applied"),
    )
    history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    runner = MigrationRunner(history)
    context = MigrationContext(data_dir=data_dir)

    assert runner.apply((step,), context) == ("r001",)
    assert runner.apply((step,), context) == ()
    assert calls == ["applied"]


def test_failed_migration_requires_explicit_restart_safe_resume(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    attempts = 0

    def apply(context: MigrationContext) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("fixture failure")

    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="restart-safe failure fixture",
        apply=apply,
        restart_safe=True,
    )
    history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    runner = MigrationRunner(history)

    with pytest.raises(MigrationError, match="fixture failure"):
        runner.apply((step,), MigrationContext(data_dir=data_dir))
    assert history.records()[0].status is MigrationStatus.FAILED
    with pytest.raises(MigrationError, match="explicit resume required"):
        runner.apply((step,), MigrationContext(data_dir=data_dir))

    assert runner.apply((step,), MigrationContext(data_dir=data_dir), resume_failed=True) == (
        "r001",
    )
    assert history.records()[0].status is MigrationStatus.APPLIED


def test_changed_migration_checksum_is_rejected(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    first = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="original",
        apply=lambda context: None,
    )
    changed = replace(first, description="changed after publication")
    runner = MigrationRunner(JsonMigrationHistoryStore.for_data_dir(data_dir))
    runner.apply((first,), MigrationContext(data_dir=data_dir))

    with pytest.raises(MigrationError, match="checksum changed"):
        runner.apply((changed,), MigrationContext(data_dir=data_dir))


def test_preflight_rejects_unsupported_direct_upgrade_path(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="only supported edge",
        apply=lambda context: None,
    )
    report = _preflight(data_dir, MigrationRegistry((step,))).run(
        PreflightRequest(
            data_dir=data_dir,
            current=_versions(platform="0.0.0", schema="0.8", revision="baseline"),
            target=_versions(platform="0.0.1", schema="1.0", revision="r001"),
        )
    )

    assert not report.ok
    assert any(check.code == "migration.path.unsupported" for check in report.checks)
    assert JsonMigrationHistoryStore.for_data_dir(data_dir).records() == ()


def test_preflight_is_dry_run_and_does_not_apply_migrations(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    marker = data_dir / "must-not-exist"
    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="dry-run fixture",
        apply=lambda context: marker.write_text("mutated", encoding="utf-8"),
    )
    report = _preflight(data_dir, MigrationRegistry((step,))).run(
        PreflightRequest(
            data_dir=data_dir,
            current=_versions(platform="0.0.0", schema="0.9", revision="baseline"),
            target=_versions(platform="0.0.1", schema="1.0", revision="r001"),
        )
    )

    assert report.ok
    assert report.planned_revisions == ("r001",)
    assert not marker.exists()
    assert JsonMigrationHistoryStore.for_data_dir(data_dir).records() == ()


def test_required_plugin_incompatibility_blocks_preflight(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    manifest = PluginManifest(
        plugin_id="required.plugin",
        name="Required Plugin",
        description="fixture",
        plugin_version="1.0",
        author="test",
        provenance=PluginProvenance(source="fixture", license="MIT"),
        extensions=(),
        supported_platform=VersionRange(maximum="0.0.0"),
    )
    current = current_release_versions()
    report = _preflight(data_dir, MigrationRegistry()).run(
        PreflightRequest(
            data_dir=data_dir,
            current=current,
            target=current,
            plugins=(manifest,),
            required_plugin_ids=frozenset({manifest.plugin_id}),
        )
    )

    assert not report.ok
    assert any(check.code == "plugin.incompatible" for check in report.checks)


def test_optional_incompatible_adapter_is_disabled_candidate_not_blocker(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    current = current_release_versions()
    report = _preflight(data_dir, MigrationRegistry()).run(
        PreflightRequest(
            data_dir=data_dir,
            current=current,
            target=current,
            adapters=(
                ExtensionCompatibilitySpec(
                    extension_id="optional.adapter",
                    installed_version="1.0",
                    required=False,
                    supported_platform_max="0.0.0",
                ),
            ),
        )
    )

    assert report.ok
    warning = next(check for check in report.checks if check.code == "adapter.incompatible")
    assert warning.severity is CheckSeverity.WARNING


def test_configuration_and_historical_event_incompatibility_block_preflight(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    current = current_release_versions()
    report = _preflight(data_dir, MigrationRegistry()).run(
        PreflightRequest(
            data_dir=data_dir,
            current=current,
            target=current,
            config_schema_versions={"runtime": ("1", "2")},
            historical_event_schema_versions=frozenset({"1.0", "9.0"}),
        )
    )

    assert not report.ok
    codes = {check.code for check in report.checks}
    assert "config.schema.changed" in codes
    assert "history.event_schema.unsupported" in codes


def test_old_portable_and_template_formats_need_explicit_translators(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    current = current_release_versions()
    history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    portable = FormatTranslatorRegistry(current.portable_format)
    portable.register("0.9", current.portable_format, lambda payload: payload)
    template = FormatTranslatorRegistry(current.template_schema)
    template.register("0", current.template_schema, lambda payload: payload)
    preflight = UpgradePreflight(
        MigrationRegistry(),
        history,
        portable_translators=portable,
        template_translators=template,
    )

    supported = preflight.run(
        PreflightRequest(
            data_dir=data_dir,
            current=current,
            target=current,
            portable_package_versions=frozenset({"0.9"}),
            template_package_versions=frozenset({"0"}),
        )
    )
    unsupported = preflight.run(
        PreflightRequest(
            data_dir=data_dir,
            current=current,
            target=current,
            portable_package_versions=frozenset({"0.8"}),
        )
    )

    assert supported.ok
    assert not unsupported.ok
    assert any(check.code == "portable.unsupported" for check in unsupported.checks)


def test_forward_only_migration_requires_matching_verified_backup(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    old = _versions(platform="0.0.0", schema="0.9", revision="baseline")
    target = _versions(platform="0.0.1", schema="1.0", revision="r001")
    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="forward-only fixture",
        apply=lambda context: None,
        backup_required=True,
        rollback_mode=RollbackMode.RESTORE_REQUIRED,
    )

    def verifier(path: Path) -> BackupVerification:
        return BackupVerification(
            backup_dir=path,
            files_checked=1,
            bytes_checked=1,
            manifest={"platform": {"version": old.platform_release}},
        )

    preflight = _preflight(data_dir, MigrationRegistry((step,)), backup_verifier=verifier)
    without_backup = preflight.run(PreflightRequest(data_dir=data_dir, current=old, target=target))
    with_backup = preflight.run(
        PreflightRequest(
            data_dir=data_dir,
            current=old,
            target=target,
            backup_dir=tmp_path / "backup",
        )
    )

    assert not without_backup.ok
    assert with_backup.ok
    assert with_backup.backup_required


def test_failed_upgrade_stays_in_maintenance_until_explicit_resume(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    old = _versions(platform="0.0.0", schema="0.9", revision="baseline")
    target = _versions(platform="0.0.1", schema="1.0", revision="r001")
    attempts = 0

    def action(context: MigrationContext) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("interrupted")

    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="maintenance fixture",
        apply=action,
        restart_safe=True,
    )
    registry = MigrationRegistry((step,))
    migration_history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    state = JsonVersionStateStore.for_data_dir(data_dir)
    state.initialize(old)
    maintenance = MaintenanceStateStore.for_data_dir(data_dir)
    service = UpgradeService(
        migrations=registry,
        runner=MigrationRunner(migration_history),
        preflight=_preflight(data_dir, registry),
        version_state=state,
        maintenance=maintenance,
        history=JsonUpgradeHistoryStore.for_data_dir(data_dir),
    )
    request = PreflightRequest(data_dir=data_dir, current=old, target=target)

    with pytest.raises(MigrationError, match="interrupted"):
        service.apply(request, quiesced=True)
    assert maintenance.active()
    assert state.read() == old

    result = service.apply(
        replace(request, resume_failed=True),
        quiesced=True,
        resume_failed=True,
    )
    assert result.current == target
    assert not maintenance.active()
    assert state.read() == target


def test_migration_requires_explicit_quiesced_workflow(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    old = _versions(platform="0.0.0", schema="0.9", revision="baseline")
    target = _versions(platform="0.0.1", schema="1.0", revision="r001")
    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="maintenance required",
        apply=lambda context: None,
    )
    registry = MigrationRegistry((step,))
    history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    state = JsonVersionStateStore.for_data_dir(data_dir)
    state.initialize(old)
    service = UpgradeService(
        migrations=registry,
        runner=MigrationRunner(history),
        preflight=_preflight(data_dir, registry),
        version_state=state,
        maintenance=MaintenanceStateStore.for_data_dir(data_dir),
        history=JsonUpgradeHistoryStore.for_data_dir(data_dir),
    )

    with pytest.raises(UpgradeError, match="quiesced"):
        service.apply(
            PreflightRequest(data_dir=data_dir, current=old, target=target),
            quiesced=False,
        )

    assert history.records() == ()
    assert state.read() == old
