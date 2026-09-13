from __future__ import annotations

from dataclasses import replace
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


def test_failed_migration_precondition_blocks_preflight_and_runner_before_mutation(
    tmp_path: Path,
) -> None:
    data_dir = _data_dir(tmp_path)
    mutations: list[str] = []

    def precondition(context: MigrationContext) -> None:
        assert context.data_dir == data_dir
        raise ValueError("required source invariant is absent")

    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="precondition fixture",
        apply=lambda context: mutations.append("mutated"),
        precondition=precondition,
    )
    registry = MigrationRegistry((step,))
    history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    release = current_release_versions()
    current = replace(
        release,
        platform_release="0.0.0",
        domain_schema="0.9",
        migration_revision="baseline",
    )
    target = replace(release, domain_schema="1.0", migration_revision="r001")
    preflight = UpgradePreflight(
        registry,
        history,
        portable_translators=FormatTranslatorRegistry(release.portable_format),
        template_translators=FormatTranslatorRegistry(release.template_schema),
    )

    report = preflight.run(PreflightRequest(data_dir=data_dir, current=current, target=target))

    assert not report.ok
    assert any(check.code == "migration.precondition.failed" for check in report.checks)
    assert mutations == []
    assert history.records() == ()

    with pytest.raises(MigrationError, match="precondition failed"):
        MigrationRunner(history).apply((step,), MigrationContext(data_dir=data_dir))
    assert mutations == []
    assert history.records() == ()


def test_explicit_resume_does_not_recheck_source_precondition_after_started_record(
    tmp_path: Path,
) -> None:
    data_dir = _data_dir(tmp_path)
    calls: list[str] = []

    def must_not_run(context: MigrationContext) -> None:
        raise AssertionError("source precondition must not be rechecked after mutation started")

    step = MigrationStep(
        sequence=1,
        revision="r001",
        from_schema="0.9",
        to_schema="1.0",
        description="resume precondition fixture",
        apply=lambda context: calls.append("resumed"),
        precondition=must_not_run,
        restart_safe=True,
    )
    history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    history.put(
        MigrationRecord(
            revision=step.revision,
            checksum=step.checksum,
            from_schema=step.from_schema,
            to_schema=step.to_schema,
            status=MigrationStatus.STARTED,
            started_at="2026-09-05T20:30:00+00:00",
        )
    )

    applied = MigrationRunner(history).apply(
        (step,),
        MigrationContext(data_dir=data_dir),
        resume_failed=True,
    )

    assert applied == ("r001",)
    assert calls == ["resumed"]
    record = history.get("r001")
    assert record is not None
    assert record.status is MigrationStatus.APPLIED
