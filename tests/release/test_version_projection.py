from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.release.cli import main as release_cli_main
from ai_multi_agent_platform.release.version_projection import (
    ReleaseVersionProjectionError,
    project_release_version,
)


def _write_fixture(root: Path, *, version: str = "0.0.1") -> None:
    (root / "src/ai_multi_agent_platform/release").mkdir(parents=True)
    (root / "release").mkdir(parents=True)
    (root / "src/ai_multi_agent_platform/upgrade").mkdir(parents=True)

    (root / "pyproject.toml").write_text(
        f'[project]\nname = "fixture"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src/ai_multi_agent_platform/__init__.py").write_text(
        f'__version__ = "{version}"\n',
        encoding="utf-8",
    )
    compatibility = {
        "schema_version": "2",
        "platform_release": version,
        "versions": {
            "platform_release": version,
            "domain_schema": "1.0",
        },
        "components": [],
    }
    encoded = json.dumps(compatibility, indent=2) + "\n"
    (root / "release/compatibility.json").write_text(encoded, encoding="utf-8")
    (root / "src/ai_multi_agent_platform/release/compatibility.json").write_text(
        encoded,
        encoding="utf-8",
    )
    (root / "src/ai_multi_agent_platform/upgrade/versioning.py").write_text(
        'BASELINE_ADOPTION_PLATFORM_RELEASE = "0.0.1"\n',
        encoding="utf-8",
    )


def test_version_projection_dry_run_reports_all_canonical_changes(tmp_path: Path) -> None:
    _write_fixture(tmp_path)

    report = project_release_version(tmp_path, target_version="1.0.0")

    assert report.current_version == "0.0.1"
    assert report.target_version == "1.0.0"
    assert report.written is False
    assert set(report.changed_files) == {
        "pyproject.toml",
        "src/ai_multi_agent_platform/__init__.py",
        "release/compatibility.json",
        "src/ai_multi_agent_platform/release/compatibility.json",
    }
    assert 'version = "0.0.1"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")


def test_version_projection_write_updates_only_release_owned_version_surfaces(
    tmp_path: Path,
) -> None:
    _write_fixture(tmp_path)

    report = project_release_version(tmp_path, target_version="1.0.0", write=True)

    assert report.written is True
    assert 'version = "1.0.0"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert '__version__ = "1.0.0"' in (
        tmp_path / "src/ai_multi_agent_platform/__init__.py"
    ).read_text(encoding="utf-8")

    repository = json.loads((tmp_path / "release/compatibility.json").read_text(encoding="utf-8"))
    packaged = json.loads(
        (tmp_path / "src/ai_multi_agent_platform/release/compatibility.json").read_text(
            encoding="utf-8"
        )
    )
    assert repository == packaged
    assert repository["platform_release"] == "1.0.0"
    assert repository["versions"]["platform_release"] == "1.0.0"

    historical = (tmp_path / "src/ai_multi_agent_platform/upgrade/versioning.py").read_text(
        encoding="utf-8"
    )
    assert 'BASELINE_ADOPTION_PLATFORM_RELEASE = "0.0.1"' in historical


def test_version_projection_refuses_to_hide_preexisting_version_drift(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    runtime = tmp_path / "src/ai_multi_agent_platform/__init__.py"
    runtime.write_text('__version__ = "0.0.2"\n', encoding="utf-8")
    before = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")

    with pytest.raises(ReleaseVersionProjectionError, match="inconsistent before projection"):
        project_release_version(tmp_path, target_version="1.0.0", write=True)

    assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == before


@pytest.mark.parametrize("version", ["1", "v1.0.0", "1.0", "release-1.0.0"])
def test_version_projection_rejects_non_semver_target(tmp_path: Path, version: str) -> None:
    _write_fixture(tmp_path)

    with pytest.raises(ReleaseVersionProjectionError, match="SemVer"):
        project_release_version(tmp_path, target_version=version)


def test_version_projection_cli_dry_run_is_machine_readable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_fixture(tmp_path)

    code = release_cli_main(
        [
            "version-project",
            "--root",
            str(tmp_path),
            "--version",
            "1.0.0",
            "--json",
        ]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["current_version"] == "0.0.1"
    assert payload["target_version"] == "1.0.0"
    assert payload["written"] is False
    assert set(payload["changed_files"]) == {
        "pyproject.toml",
        "src/ai_multi_agent_platform/__init__.py",
        "release/compatibility.json",
        "src/ai_multi_agent_platform/release/compatibility.json",
    }
