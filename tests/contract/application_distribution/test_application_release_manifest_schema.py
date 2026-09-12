from __future__ import annotations

import json
from pathlib import Path


def test_manifest_schema_uses_platform_neutral_identifier() -> None:
    schema_path = (
        Path(__file__).parents[3] / "docs" / "schemas" / "application-release-manifest.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["$id"] == "urn:ai-multi-agent-platform:schema:application-release-manifest:v1"
