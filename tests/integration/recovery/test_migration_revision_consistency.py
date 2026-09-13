from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ai_multi_agent_platform.upgrade import (
    FormatTranslatorRegistry,
    JsonMigrationHistoryStore,
    MigrationRegistry,
    MigrationStep,
    PreflightRequest,
    UpgradePreflight,
    current_release_versions,
)


def _preflight(data_dir: Path, registry: MigrationRegistry) -> UpgradePreflight:
    current = current_release_versions()
    return UpgradePreflight(
        registry,
        JsonMigrationHistoryStore.for_data_dir(data_dir),
        portable_translators=FormatTranslatorRegistry(current.portable_format),
        template_translators=FormatTranslatorRegistry(current.template_schema),
    )


def test_target_migration_revision_must_match_planned_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    release = current_release_versions()
    current = replace(
        release,
        platform_release="0.0.0",
        domain_schema="0.9",
        migration_revision="baseline",
    )
    target = replace(release, domain_schema="1.0", migration_revision="wrong-revision")
    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="revision-consistency fixture",
        apply=lambda context: None,
    )

    report = _preflight(data_dir, MigrationRegistry((step,))).run(
        PreflightRequest(data_dir=data_dir, current=current, target=target)
    )

    assert not report.ok
    check = next(
        item for item in report.checks if item.code == "migration.revision.target_mismatch"
    )
    assert check.details["expected_revision"] == "r001"
    assert check.details["target_revision"] == "wrong-revision"


def test_active_nonbaseline_revision_requires_applied_history(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    current = current_release_versions(migration_revision="r001")

    report = _preflight(data_dir, MigrationRegistry()).run(
        PreflightRequest(data_dir=data_dir, current=current, target=current)
    )

    assert not report.ok
    check = next(
        item for item in report.checks if item.code == "migration.revision.current_unproven"
    )
    assert check.details["current_revision"] == "r001"
