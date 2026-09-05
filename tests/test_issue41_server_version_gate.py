from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ai_multi_agent_platform.deployment.server import main
from ai_multi_agent_platform.upgrade import JsonVersionStateStore, current_release_versions


def test_platform_server_rejects_persisted_release_mismatch_before_building_deployment(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    runtime = current_release_versions()
    persisted = replace(runtime, platform_release="0.0.0")
    JsonVersionStateStore.for_data_dir(data_dir).initialize(persisted)
    monkeypatch.setenv("AI_MAP_DATA_DIR", str(data_dir))
    built = False

    def forbidden_builder(config):
        nonlocal built
        built = True
        raise AssertionError("deployment must not be built for incompatible version state")

    code = main(["serve"], deployment_builder=forbidden_builder)

    assert code == 3
    assert not built
    stderr = capsys.readouterr().err
    assert "running platform release is incompatible" in stderr
    assert "platform_release" in stderr
