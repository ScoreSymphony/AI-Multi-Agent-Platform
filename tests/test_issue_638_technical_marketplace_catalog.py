from pathlib import Path

import pytest
from jsonschema import ValidationError

from ai_multi_agent_platform.distribution import (
    CuratedCandidateReview,
    DiscoveryCandidate,
    DistributionRoute,
    FilesystemRegistryProvider,
    LocalRegistryProvider,
    RegistryItem,
    RegistryItemType,
    RegistryQuery,
    RegistrySource,
    TrustStatus,
    curate_discovered_candidate,
    derive_technical_metadata,
    registry_item_from_document,
)

CATALOG = Path(__file__).parents[1] / "catalogs" / "technical-components" / "catalog.json"
EXPECTED_CODE_INTELLIGENCE = {
    "projectatlas",
    "graphify",
    "codegraph",
    "understand-anything",
}
EXPECTED_CODING_AGENTS = {"openhands", "aider"}
EXPECTED_AGENT_FRAMEWORKS = {"pydantic-ai", "langgraph", "smolagents", "google-adk"}
EXPECTED_MEMORY = {"mem0", "graphiti", "qdrant"}
EXPECTED_INFERENCE = {"llama-cpp", "ollama", "vllm"}


def test_curated_technical_catalog_loads_cross_category_seed() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    all_items = provider.search(RegistryQuery(technical_only=True))

    assert len(all_items) >= 22
    assert {
        item.item_id
        for item in provider.search(RegistryQuery(categories=frozenset({"code-intelligence"})))
    } == EXPECTED_CODE_INTELLIGENCE
    assert {
        item.item_id
        for item in provider.search(RegistryQuery(categories=frozenset({"coding-agent"})))
    } == EXPECTED_CODING_AGENTS
    assert {
        item.item_id
        for item in provider.search(RegistryQuery(categories=frozenset({"agent-framework"})))
    } == EXPECTED_AGENT_FRAMEWORKS
    assert EXPECTED_MEMORY.issubset(
        {
            item.item_id
            for item in provider.search(RegistryQuery(categories=frozenset({"memory-and-context"})))
        }
    )
    assert {
        item.item_id
        for item in provider.search(RegistryQuery(categories=frozenset({"inference-runtime"})))
    } == EXPECTED_INFERENCE


def test_curated_catalog_entries_remain_manual_untrusted_candidates() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    for item in provider.search(RegistryQuery(technical_only=True)):
        technical = derive_technical_metadata(item)
        assert technical is not None
        assert item.route is DistributionRoute.MANUAL
        assert item.trust_status is TrustStatus.UNTRUSTED
        assert technical.lifecycle_status == "candidate"
        assert technical.evaluation_status == "required"
        assert technical.evaluation_required is True
        assert technical.cost_status in {"compatible", "conditional", "unknown"}
        assert technical.network_status in {"none", "optional", "unknown"}
        if item.item_id in EXPECTED_CODE_INTELLIGENCE:
            assert (
                item.review_reference
                == "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/issues/502"
            )
        else:
            assert (
                item.review_reference
                == "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/issues/638"
            )


def test_curated_catalog_preserves_explicit_unknowns_instead_of_guessing() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    projectatlas = provider.get("projectatlas")
    metadata = derive_technical_metadata(projectatlas)

    assert metadata is not None
    assert metadata.deployment_modes == ("unknown",)
    assert metadata.cost_status == "unknown"
    assert metadata.network_status == "unknown"


def test_verified_external_catalog_records_do_not_fake_upstream_revision() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    for item_id in {
        "openhands",
        "aider",
        "pydantic-ai",
        "langgraph",
        "smolagents",
        "google-adk",
        "spec-kit",
        "mem0",
        "graphiti",
        "qdrant",
        "promptfoo",
        "lighteval",
        "browser-use",
        "playwright",
        "llama-cpp",
        "ollama",
        "vllm",
        "sentence-transformers",
    }:
        item = provider.get(item_id)
        assert item.source.revision is None
        assert "upstream version is not pinned" in (item.changelog or "")
        assert "project-status:not-archived" in item.tags


def test_curated_candidate_artifacts_are_reference_only() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    for item in provider.search(RegistryQuery(technical_only=True)):
        artifact = provider.fetch_artifact(item.item_id, item.version).decode("utf-8")
        normalized = artifact.casefold()
        assert "does not vendor" in normalized
        assert "bundle" in normalized
        assert "install" in normalized
        assert "execute" in normalized


def test_technical_only_query_excludes_generic_registry_assets() -> None:
    technical = RegistryItem(
        item_id="technical-tool",
        item_type=RegistryItemType.TOOL,
        name="Technical Tool",
        description="Technical fixture",
        version="1.0.0",
        publisher="example",
        source=RegistrySource(
            repository="https://example.invalid/technical",
            package_reference="technical@1.0.0",
        ),
        license="MIT",
        provenance="test fixture",
        categories=frozenset({"evaluation"}),
    )
    connector = RegistryItem(
        item_id="calendar-connector",
        item_type=RegistryItemType.CONNECTOR,
        name="Calendar Connector",
        description="Generic Registry fixture",
        version="1.0.0",
        publisher="example",
        source=RegistrySource(
            repository="https://example.invalid/connector",
            package_reference="connector@1.0.0",
        ),
        license="MIT",
        provenance="test fixture",
        categories=frozenset({"productivity"}),
    )
    provider = LocalRegistryProvider((technical, connector))

    assert provider.search(RegistryQuery()) == (connector, technical)
    assert provider.search(RegistryQuery(technical_only=True)) == (technical,)


def test_discovery_source_cannot_promote_without_explicit_review() -> None:
    candidate = DiscoveryCandidate(
        source_id="mcp-registry",
        external_id="example/tool",
        name="Example Tool",
        source_repository="https://github.com/example/tool",
        license="MIT",
    )
    review = CuratedCandidateReview(
        item_id="example-tool",
        item_type=RegistryItemType.TOOL,
        description="Reviewed discovery fixture",
        version="1.0.0",
        publisher="example",
        source_repository="https://github.com/example/tool",
        package_reference="github:example/tool",
        license="MIT",
        provenance="reviewed fixture",
        categories=frozenset({"code-intelligence"}),
        deployment_modes=frozenset({"local"}),
        cost_status="compatible",
        network_status="optional",
        review_reference="https://example.invalid/review",
    )

    item = curate_discovered_candidate(candidate, review)
    technical = derive_technical_metadata(item)

    assert item.route is DistributionRoute.MANUAL
    assert item.trust_status is TrustStatus.UNTRUSTED
    assert "discovery-source:mcp-registry" in item.tags
    assert technical is not None
    assert technical.lifecycle_status == "candidate"
    assert technical.evaluation_status == "required"


def test_discovery_review_fails_closed_on_source_or_license_mismatch() -> None:
    candidate = DiscoveryCandidate(
        source_id="external",
        external_id="example/tool",
        name="Example Tool",
        source_repository="https://github.com/example/tool",
        license="MIT",
    )
    base = dict(
        item_id="example-tool",
        item_type=RegistryItemType.TOOL,
        description="Reviewed discovery fixture",
        version="1.0.0",
        publisher="example",
        package_reference="github:example/tool",
        provenance="reviewed fixture",
        categories=frozenset({"code-intelligence"}),
    )

    with pytest.raises(ValueError, match="source_repository"):
        curate_discovered_candidate(
            candidate,
            CuratedCandidateReview(
                **base,
                source_repository="https://github.com/other/tool",
                license="MIT",
            ),
        )

    with pytest.raises(ValueError, match="license"):
        curate_discovered_candidate(
            candidate,
            CuratedCandidateReview(
                **base,
                source_repository="https://github.com/example/tool",
                license="Apache-2.0",
            ),
        )


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
