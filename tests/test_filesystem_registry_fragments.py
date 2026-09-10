import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.distribution import FilesystemRegistryProvider, RegistryQuery


def _metadata(item_id: str) -> dict[str, object]:
    return {
        "schema_version": "2",
        "item_id": item_id,
        "item_type": "tool",
        "name": item_id,
        "description": "fragment fixture",
        "version": "1.0.0",
        "publisher": "test",
        "source": {
            "repository": f"https://example.invalid/{item_id}",
            "package_reference": f"{item_id}@1.0.0",
            "revision": None,
        },
        "license": "MIT",
        "provenance": "test fixture",
        "supported_platform": {},
        "dependencies": [],
        "requested_permissions": [],
        "required_capabilities": [],
        "tags": [
            "lifecycle:candidate",
            "evaluation:required",
            "deployment:unknown",
            "cost:unknown",
            "network:unknown",
        ],
        "categories": ["evaluation"],
        "distribution_route": "manual",
        "integrity": {},
        "trust_status": "untrusted",
        "deprecated": False,
        "yanked": False,
    }


def _write_catalog(
    path: Path,
    item_ids: tuple[str, ...],
    *,
    provider_id: str = "fixture",
    schema_version: str = "1",
) -> None:
    document = {
        "schema_version": schema_version,
        "provider_id": provider_id,
        "items": [
            {"metadata": _metadata(item_id), "artifact": "artifacts/reference.md"}
            for item_id in item_ids
        ],
    }
    path.write_text(json.dumps(document), encoding="utf-8")


def test_filesystem_registry_loads_deterministic_sibling_fragments(tmp_path: Path) -> None:
    primary = tmp_path / "catalog.json"
    _write_catalog(primary, ("base",))
    _write_catalog(tmp_path / "catalog.fragment.z.json", ("zeta",))
    _write_catalog(tmp_path / "catalog.fragment.a.json", ("alpha",))
    artifact = tmp_path / "artifacts" / "reference.md"
    artifact.parent.mkdir()
    artifact.write_text("reference", encoding="utf-8")

    provider = FilesystemRegistryProvider(primary)

    assert provider.provider_id == "fixture"
    assert tuple(item.item_id for item in provider.search(RegistryQuery())) == (
        "alpha",
        "base",
        "zeta",
    )
    assert provider.fetch_artifact("alpha", "1.0.0") == b"reference"


def test_filesystem_registry_fragment_provider_mismatch_fails_closed(tmp_path: Path) -> None:
    primary = tmp_path / "catalog.json"
    _write_catalog(primary, ("base",), provider_id="primary")
    _write_catalog(
        tmp_path / "catalog.fragment.other.json",
        ("other",),
        provider_id="different",
    )

    with pytest.raises(ValueError, match="fragment provider_id must match"):
        FilesystemRegistryProvider(primary)


def test_filesystem_registry_duplicate_identity_across_fragment_fails_closed(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "catalog.json"
    _write_catalog(primary, ("duplicate",))
    _write_catalog(tmp_path / "catalog.fragment.a.json", ("duplicate",))

    with pytest.raises(ValueError, match="duplicate item/version"):
        FilesystemRegistryProvider(primary)


def test_filesystem_registry_fragment_schema_validation_is_not_weakened(tmp_path: Path) -> None:
    primary = tmp_path / "catalog.json"
    _write_catalog(primary, ("base",))
    _write_catalog(
        tmp_path / "catalog.fragment.invalid.json",
        ("invalid",),
        schema_version="999",
    )

    with pytest.raises(ValueError, match="unsupported registry catalog schema_version"):
        FilesystemRegistryProvider(primary)
