from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_BOUNDARIES = ROOT / "docs" / "PACKAGE_BOUNDARIES.toml"
FEATURE_CLASSIFICATION = ROOT / "docs" / "FEATURE_CLASSIFICATION.toml"


def _load(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def test_managed_application_lifecycle_has_a_distinct_canonical_owner() -> None:
    entries = _load(PACKAGE_BOUNDARIES)["package"]
    packages = {entry["name"]: entry for entry in entries}

    assert packages["applications"]["kind"] == "domain"
    assert packages["applications"]["owner"] == "applications"
    assert packages["application_distribution"]["owner"] == "application_distribution"
    assert packages["distribution"]["owner"] == "distribution"
    assert packages["distributed"]["owner"] == "distributed"
    assert packages["deployment"]["owner"] == "repository"


def test_application_adapters_are_classified_as_beta_optional_advanced() -> None:
    features = _load(FEATURE_CLASSIFICATION)["feature"]
    feature = next(item for item in features if item["id"] == "application-adapters")

    assert feature["owners"] == ["applications"]
    assert feature["role"] == "optional_advanced"
    assert feature["stability"] == "beta"
    assert feature["user_signaling"] == "documentation_and_ui_contextual"
