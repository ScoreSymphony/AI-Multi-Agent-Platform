from __future__ import annotations

from pathlib import Path

from ai_multi_agent_platform.plugins.models import PluginManifest, PluginProvenance
from ai_multi_agent_platform.upgrade import (
    FormatTranslatorRegistry,
    JsonMigrationHistoryStore,
    MigrationRegistry,
    PreflightRequest,
    UpgradePreflight,
    current_release_versions,
)


def _preflight(data_dir: Path) -> UpgradePreflight:
    current = current_release_versions()
    return UpgradePreflight(
        MigrationRegistry(),
        JsonMigrationHistoryStore.for_data_dir(data_dir),
        portable_translators=FormatTranslatorRegistry(current.portable_format),
        template_translators=FormatTranslatorRegistry(current.template_schema),
    )


def test_required_plugin_with_unsupported_manifest_version_blocks_preflight(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    current = current_release_versions()
    manifest = PluginManifest(
        plugin_id="legacy.plugin",
        name="Legacy Plugin",
        description="fixture with an unsupported manifest contract",
        plugin_version="1.0",
        author="test",
        provenance=PluginProvenance(source="fixture", license="MIT"),
        extensions=(),
        manifest_version="0",
    )

    report = _preflight(data_dir).run(
        PreflightRequest(
            data_dir=data_dir,
            current=current,
            target=current,
            plugins=(manifest,),
            required_plugin_ids=frozenset({manifest.plugin_id}),
        )
    )

    assert not report.ok
    check = next(item for item in report.checks if item.code == "plugin.incompatible")
    assert check.details["manifest_version"] == "0"
    reasons = check.details["reasons"]
    assert isinstance(reasons, list)
    assert any(isinstance(reason, str) and "manifest version" in reason for reason in reasons)


def test_missing_required_plugin_manifest_blocks_preflight(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    current = current_release_versions()

    report = _preflight(data_dir).run(
        PreflightRequest(
            data_dir=data_dir,
            current=current,
            target=current,
            required_plugin_ids=frozenset({"required.plugin"}),
        )
    )

    assert not report.ok
    check = next(item for item in report.checks if item.code == "plugin.required_missing")
    assert check.details["plugin_ids"] == ["required.plugin"]
