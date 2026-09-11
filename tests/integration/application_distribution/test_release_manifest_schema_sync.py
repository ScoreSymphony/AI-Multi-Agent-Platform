from __future__ import annotations

import json
from pathlib import Path

from ai_multi_agent_platform.application_distribution import APPLICATION_RELEASE_MANIFEST_SCHEMA


def test_runtime_release_manifest_schema_exactly_matches_documented_contract() -> None:
    root = Path(__file__).parents[3]
    documented = json.loads(
        (root / "docs" / "schemas" / "application-release-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert APPLICATION_RELEASE_MANIFEST_SCHEMA == documented
