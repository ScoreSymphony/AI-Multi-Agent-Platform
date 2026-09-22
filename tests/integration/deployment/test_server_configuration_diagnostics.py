from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.single_node_app import main as single_node_main
from ai_multi_agent_platform.deployment.server import main as server_main


def test_invalid_environment_configuration_is_actionable_without_traceback(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("AI_MAP_PORT", "not-a-port")

    code = server_main(["serve"])

    assert code == 2
    stderr = capsys.readouterr().err
    assert "single-node configuration error" in stderr
    assert "AI_MAP_PORT must be an integer" in stderr
    assert "config/single-node.env.example" in stderr
    assert "Traceback" not in stderr


@pytest.mark.parametrize(
    ("catalog_document", "expected"),
    (
        (None, "invalid or unavailable"),
        ("{", "invalid or unavailable"),
        (
            json.dumps({"schema_version": "999", "provider_id": "custom", "items": []}),
            "unsupported registry catalog schema_version",
        ),
    ),
)
def test_invalid_custom_registry_catalog_fails_closed_with_operator_diagnostic(
    tmp_path: Path,
    monkeypatch,
    capsys,
    catalog_document: str | None,
    expected: str,
) -> None:
    data_dir = tmp_path / "data"
    catalog = tmp_path / "custom-registry.json"
    if catalog_document is not None:
        catalog.write_text(catalog_document, encoding="utf-8")

    monkeypatch.setenv("AI_MAP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AI_MAP_REGISTRY_CATALOG", str(catalog))

    code = single_node_main(["smoke"])

    assert code == 2
    stderr = capsys.readouterr().err
    assert "single-node configuration error" in stderr
    assert "AI_MAP_REGISTRY_CATALOG" in stderr
    assert str(catalog) in stderr
    assert expected in stderr
    assert "Traceback" not in stderr
    assert "platform-starter" not in stderr
