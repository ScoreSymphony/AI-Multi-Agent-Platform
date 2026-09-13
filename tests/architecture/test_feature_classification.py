from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "docs" / "FEATURE_CLASSIFICATION.toml"
DOCUMENT_PATH = ROOT / "docs" / "FEATURE_CLASSIFICATION.md"
PACKAGE_BOUNDARIES_PATH = ROOT / "docs" / "PACKAGE_BOUNDARIES.toml"

ALLOWED_ROLES = {"core", "platform_extension", "optional_advanced"}
ALLOWED_STABILITIES = {"stable", "beta", "experimental"}
ALLOWED_USER_SIGNALING = {"none", "documentation", "documentation_and_ui_contextual"}


def _load(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _features() -> list[dict[str, Any]]:
    registry = _load(REGISTRY_PATH)
    assert registry.get("schema_version") == 1
    features = registry.get("feature")
    assert isinstance(features, list) and features, (
        "FEATURE_CLASSIFICATION.toml must define [[feature]] entries"
    )
    return features


def test_feature_classification_registry_is_well_formed() -> None:
    features = _features()
    ids = [feature.get("id") for feature in features]

    assert all(isinstance(feature_id, str) and feature_id for feature_id in ids)
    assert len(ids) == len(set(ids)), "duplicate feature classification id"

    for feature in features:
        feature_id = feature["id"]
        assert isinstance(feature.get("name"), str) and feature["name"].strip()
        assert feature.get("role") in ALLOWED_ROLES, (
            f"{feature_id}: invalid role {feature.get('role')!r}"
        )
        assert feature.get("stability") in ALLOWED_STABILITIES, (
            f"{feature_id}: invalid stability {feature.get('stability')!r}"
        )
        assert feature.get("user_signaling") in ALLOWED_USER_SIGNALING, (
            f"{feature_id}: invalid user_signaling {feature.get('user_signaling')!r}"
        )

        owners = feature.get("owners")
        surfaces = feature.get("public_surfaces")
        docs = feature.get("docs")
        compatibility = feature.get("compatibility")
        assert isinstance(owners, list) and owners, f"{feature_id}: owners are required"
        assert isinstance(surfaces, list) and surfaces, (
            f"{feature_id}: public_surfaces are required"
        )
        assert isinstance(docs, list) and docs, f"{feature_id}: docs are required"
        assert isinstance(compatibility, str) and len(compatibility.strip()) >= 40, (
            f"{feature_id}: compatibility expectation is missing or too vague"
        )


def test_feature_owners_reference_canonical_package_owners() -> None:
    package_entries = _load(PACKAGE_BOUNDARIES_PATH).get("package")
    assert isinstance(package_entries, list)
    canonical_owners = {entry["owner"] for entry in package_entries}

    for feature in _features():
        unknown = set(feature["owners"]) - canonical_owners
        assert not unknown, f"{feature['id']}: unknown canonical owner(s): {sorted(unknown)}"


def test_feature_documentation_paths_exist() -> None:
    for feature in _features():
        for document in feature["docs"]:
            assert isinstance(document, str) and document
            path = ROOT / document
            assert path.is_file(), f"{feature['id']}: missing documentation path {document!r}"


def test_human_readable_audit_covers_every_registry_entry() -> None:
    text = DOCUMENT_PATH.read_text(encoding="utf-8")
    for feature in _features():
        assert f"`{feature['id']}`" in text, (
            f"{feature['id']}: missing from human-readable feature audit"
        )


def test_non_stable_surfaces_have_discoverable_maturity_signaling() -> None:
    for feature in _features():
        if feature["stability"] == "stable":
            continue
        assert feature["user_signaling"] != "none", (
            f"{feature['id']}: {feature['stability']} surfaces require maturity signaling"
        )


def test_experimental_surfaces_have_concrete_labeled_entrypoints() -> None:
    for feature in _features():
        if feature["stability"] != "experimental":
            continue

        feature_id = feature["id"]
        assert feature["user_signaling"] == "documentation_and_ui_contextual", (
            f"{feature_id}: experimental surfaces require contextual UI/documentation signaling"
        )

        signaling_docs = feature.get("signaling_docs")
        assert isinstance(signaling_docs, list) and signaling_docs, (
            f"{feature_id}: experimental surfaces must name concrete signaling_docs"
        )
        for document in signaling_docs:
            assert document in feature["docs"], (
                f"{feature_id}: signaling doc {document!r} must also be authoritative documentation"
            )
            path = ROOT / document
            assert path.is_file(), f"{feature_id}: missing signaling doc {document!r}"
            text = path.read_text(encoding="utf-8").casefold()
            assert "experimental" in text, (
                f"{feature_id}: signaling doc {document!r} must visibly label "
                "the surface Experimental"
            )
