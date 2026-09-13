from __future__ import annotations

from pathlib import Path

import pytest

from ai_multi_agent_platform.upgrade import (
    FormatTranslatorRegistry,
    JsonMigrationHistoryStore,
    MigrationContext,
    MigrationError,
    MigrationRecord,
    MigrationRegistry,
    MigrationRunner,
    MigrationStatus,
    MigrationStep,
    PreflightRequest,
    UpgradePreflight,
    current_release_versions,
)


def _data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    (root / "db").mkdir(parents=True)
    return root


def _record_started(history: JsonMigrationHistoryStore, step: MigrationStep) -> None:
    history.put(
        MigrationRecord(
            revision=step.revision,
            checksum=step.checksum,
            from_schema=step.from_schema,
            to_schema=step.to_schema,
            status=MigrationStatus.STARTED,
            started_at="2026-09-05T20:10:00+00:00",
        )
    )


def test_interrupted_non_restart_safe_migration_is_never_implicitly_rerun(
    tmp_path: Path,
) -> None:
    data_dir = _data_dir(tmp_path)
    calls: list[str] = []
    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="non-restart-safe interruption fixture",
        apply=lambda context: calls.append("mutated"),
        restart_safe=False,
    )
    history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    _record_started(history, step)
    runner = MigrationRunner(history)

    with pytest.raises(MigrationError, match="explicit resume required"):
        runner.apply((step,), MigrationContext(data_dir=data_dir))
    with pytest.raises(MigrationError, match="not restart-safe"):
        runner.apply(
            (step,),
            MigrationContext(data_dir=data_dir),
            resume_failed=True,
        )

    assert calls == []
    assert history.get("r001") is not None
    assert history.get("r001").status is MigrationStatus.STARTED  # type: ignore[union-attr]


def test_interrupted_restart_safe_migration_requires_explicit_resume(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    calls: list[str] = []
    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="restart-safe interruption fixture",
        apply=lambda context: calls.append("mutated"),
        restart_safe=True,
    )
    history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    _record_started(history, step)
    runner = MigrationRunner(history)

    with pytest.raises(MigrationError, match="explicit resume required"):
        runner.apply((step,), MigrationContext(data_dir=data_dir))

    assert runner.apply(
        (step,),
        MigrationContext(data_dir=data_dir),
        resume_failed=True,
    ) == ("r001",)
    assert calls == ["mutated"]
    record = history.get("r001")
    assert record is not None
    assert record.status is MigrationStatus.APPLIED


def test_preflight_reports_interrupted_migration_distinctly(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="interrupted preflight fixture",
        apply=lambda context: None,
        restart_safe=True,
    )
    registry = MigrationRegistry((step,))
    history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    _record_started(history, step)
    release = current_release_versions()
    current = release.__class__(
        platform_release="0.0.0",
        domain_schema="0.9",
        api=release.api,
        migration_revision="baseline",
        plugin_manifest=release.plugin_manifest,
        portable_format=release.portable_format,
        template_schema=release.template_schema,
        backup_format=release.backup_format,
        worker_protocol=release.worker_protocol,
        message_protocol=release.message_protocol,
    )
    target = release.__class__(
        platform_release=release.platform_release,
        domain_schema="1.0",
        api=release.api,
        migration_revision="r001",
        plugin_manifest=release.plugin_manifest,
        portable_format=release.portable_format,
        template_schema=release.template_schema,
        backup_format=release.backup_format,
        worker_protocol=release.worker_protocol,
        message_protocol=release.message_protocol,
    )
    preflight = UpgradePreflight(
        registry,
        history,
        portable_translators=FormatTranslatorRegistry(release.portable_format),
        template_translators=FormatTranslatorRegistry(release.template_schema),
    )

    blocked = preflight.run(PreflightRequest(data_dir=data_dir, current=current, target=target))
    resumable = preflight.run(
        PreflightRequest(
            data_dir=data_dir,
            current=current,
            target=target,
            resume_failed=True,
        )
    )

    assert not blocked.ok
    assert any(check.code == "migration.interrupted.unresolved" for check in blocked.checks)
    assert resumable.ok
    assert any(check.code == "migration.interrupted.resumable" for check in resumable.checks)
