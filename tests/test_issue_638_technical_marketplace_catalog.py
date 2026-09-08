from pathlib import Path

import pytest
from jsonschema import ValidationError

from ai_multi_agent_platform.distribution import (
    DistributionRoute,
    FilesystemRegistryProvider,
    RegistryItem,
    RegistryItemType,
    RegistryQuery,
    RegistrySource,
    TrustStatus,
    registry_item_from_document,
)


CATALOG = Path(__file__).parents[1] / "catalogs" / "technical-components" / "catalog.json"
EXPECTED_CODE_INTELLIGENCE = {
    "projectatlas",
    "graphify",
    "codegraph",
    "understand-anything",
}


def test_curated_technical_catalog_loads_and_exposes_code_intelligence_candidates() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    items = provider.search(RegistryQuery(categories=frozenset({"code-intelligence"})))

    assert {item.item_id for item in items} == EXPECTED_CODE_INTELLIGENCE
    assert all(item.item_type is RegistryItemType.TOOL for item in items)
    assert all(item.route is DistributionRoute.MANUAL for item in items)
    assert all(item.trust_status is TrustStatus.UNTRUSTED for item in items)
    assert all("candidate" in item.tags for item in items)


def test_curated_candidate_artifacts_are_reference_only() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    for item_id in EXPECTED_CODE_INTELLIGENCE:
        item = provider.get(item_id)
        artifact = provider.fetch_artifact(item.item_id, item.version).decode("utf-8")
        assert "Status: CANDIDATE" in artifact
        assert "discovery/evaluation only" in artifact
        assert "does not vendor, bundle, install or execute" in artifact


def test_manual_route_override_does_not_change_existing_tool_default() -> None:
    item = RegistryItem(
        item_id="example-tool",
        item_type=RegistryItemType.TOOL,
        name="Example Tool",
        description="Portable tool fixture",
        version="1.0.0",
        publisher="example",
        source=RegistrySource(
            repository="https://example.invalid/tool",
            package_reference="example-tool@1.0.0",
        ),
        license="MIT",
        provenance="test fixture",
    )

    assert item.route is DistributionRoute.PORTABLE_IMPORT


def test_only_manual_distribution_route_can_override_type_routing() -> None:
    with pytest.raises(ValueError, match="may only select the manual route"):
        RegistryItem(
            item_id="invalid-route-tool",
            item_type=RegistryItemType.TOOL,
            name="Invalid Route Tool",
            description="Invalid route fixture",
            version="1.0.0",
            publisher="example",
            source=RegistrySource(
                repository="https://example.invalid/tool",
                package_reference="invalid-route-tool@1.0.0",
            ),
            license="MIT",
            provenance="test fixture",
            distribution_route=DistributionRoute.PLUGIN,
        )


def test_registry_item_v1_documents_remain_readable() -> None:
    document = _registry_document(schema_version="1")

    item = registry_item_from_document(document)

    assert item.route is DistributionRoute.PORTABLE_IMPORT


def test_registry_item_v2_accepts_manual_route_and_rejects_other_overrides() -> None:
    document = _registry_document(schema_version="2")
    document["distribution_route"] = "manual"

    item = registry_item_from_document(document)
    assert item.route is DistributionRoute.MANUAL

    document["distribution_route"] = "portable_import"
    with pytest.raises(ValidationError):
        registry_item_from_document(document)


def test_registry_item_v1_rejects_v2_manual_route_field() -> None:
    document = _registry_document(schema_version="1")
    document["distribution_route"] = "manual"

    with pytest.raises(ValidationError):
        registry_item_from_document(document)


def _registry_document(*, schema_version: str) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "item_id": "candidate-tool",
        "item_type": "tool",
        "name": "Candidate Tool",
        "description": "Discovery-only candidate",
        "version": "1.0.0",
        "publisher": "example",
        "source": {
            "repository": "https://example.invalid/candidate",
            "package_reference": "candidate-tool@1.0.0",
        },
        "license": "MIT",
        "provenance": "test fixture",
        "supported_platform": {},
        "dependencies": [],
        "requested_permissions": [],
        "required_capabilities": [],
        "integrity": {},
        "trust_status": "untrusted",
        "deprecated": False,
        "yanked": False,
    }
