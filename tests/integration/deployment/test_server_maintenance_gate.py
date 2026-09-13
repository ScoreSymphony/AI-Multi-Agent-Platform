from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ai_multi_agent_platform.deployment.server import main
from ai_multi_agent_platform.upgrade import (
    MaintenanceState,
    MaintenanceStateStore,
    current_release_versions,
)


def test_platform_server_does_not_build_deployment_during_upgrade_maintenance(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    release = current_release_versions()
    source = replace(
        release,
        platform_release="0.0.0",
        domain_schema="0.9",
        migration_revision="baseline",
    )
    target = replace(release, migration_revision="r001")
    MaintenanceStateStore.for_data_dir(data_dir).enter(
        MaintenanceState(
            started_at="2026-09-05T20:20:00+00:00",
            source=source,
            target=target,
            planned_revisions=("r001",),
        )
    )
    monkeypatch.setenv("AI_MAP_DATA_DIR", str(data_dir))
    built = False

    def forbidden_builder(config):
        nonlocal built
        built = True
        raise AssertionError("deployment must not be built during upgrade maintenance")

    code = main(["serve"], deployment_builder=forbidden_builder)

    assert code == 3
    assert not built
    assert "platform upgrade maintenance is active" in capsys.readouterr().err
