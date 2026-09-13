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

CATALOG = Path(__file__).parents[3] / "catalogs" / "technical-components" / "catalog.json"
ISSUE_502_ITEMS = {
    "projectatlas",
    "graphify",
    "codegraph",
    "understand-anything",
}
EXPECTED_CODE_INTELLIGENCE = ISSUE_502_ITEMS | {
    "serena",
    "ast-grep",
    "semgrep",
    "scip",
}
EXPECTED_CODING_AGENTS = {
    "openhands",
    "aider",
    "opencode",
    "goose",
    "cline",
    "roo-code",
    "plandex",
    "gemini-cli",
    "codex-cli",
    "jcode",
    "kimi-code-cli",
    "mimo-code",
    "zcode",
    "mini-swe-agent",
    "copilot-cli",
    "claude-code",
}
EXPECTED_AGENT_FRAMEWORKS = {
    "pydantic-ai",
    "langgraph",
    "smolagents",
    "google-adk",
    "anything-llm",
    "microsoft-agent-framework",
    "agno",
    "crewai",
    "dify",
    "flowise",
    "letta",
    "paperclip",
    "agent-zero",
    "lifeos",
    "sim-studio",
    "ruflo",
    "gas-town",
    "langflow",
    "multica",
}
EXPECTED_MEMORY = {"mem0", "graphiti", "qdrant", "anything-llm", "letta", "openviking"}
EXPECTED_INFERENCE = {
    "llama-cpp",
    "ollama",
    "vllm",
    "tei",
    "onnx-runtime",
    "transformers-js",
}
EXPECTED_SPECIFICATIONS = {"spec-kit", "superpowers", "ecc", "openspec", "bmad-method"}
EXPECTED_BROWSER_EXECUTION = {"browser-use", "playwright", "stagehand", "swe-rex"}
EXPECTED_EVALUATION = {
    "promptfoo",
    "lighteval",
    "inspect-ai",
    "deepeval",
    "agentdojo",
    "garak",
    "harbor",
    "openenv",
}
EXPECTED_MUSIC_AI = {
    "bachi",
    "analysisgnn",
    "clamp3",
    "mert",
    "musvit",
    "musicbert",
    "legato",
    "transformers-js",
}
REFERENCE_ONLY = {"roo-code", "flowise", "gsd"}
DEFERRED_POLICY = {"copilot-cli", "claude-code", "musvit", "legato"}


def _ids(
    provider: FilesystemRegistryProvider,
    category: str,
    *,
    include_deprecated: bool = False,
) -> set[str]:
    return {
        item.item_id
        for item in provider.search(
            RegistryQuery(
                categories=frozenset({category}),
                include_deprecated=include_deprecated,
            )
        )
    }


def test_curated_technical_catalog_loads_cross_category_seed() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    all_items = provider.search(RegistryQuery(technical_only=True, include_deprecated=True))

    assert len(all_items) >= 78
    assert _ids(provider, "code-intelligence") == EXPECTED_CODE_INTELLIGENCE
    assert _ids(provider, "coding-agent", include_deprecated=True) == EXPECTED_CODING_AGENTS
    assert _ids(provider, "agent-framework", include_deprecated=True) == EXPECTED_AGENT_FRAMEWORKS
    assert EXPECTED_MEMORY.issubset(_ids(provider, "memory-and-context"))
    assert _ids(provider, "inference-runtime") == EXPECTED_INFERENCE
    assert _ids(provider, "specification-and-skills") == EXPECTED_SPECIFICATIONS
    assert _ids(provider, "specification-and-skills", include_deprecated=True) == (
        EXPECTED_SPECIFICATIONS | {"gsd"}
    )
    assert _ids(provider, "browser-and-execution") == EXPECTED_BROWSER_EXECUTION
    assert _ids(provider, "evaluation") == EXPECTED_EVALUATION
    assert _ids(provider, "music-ai") == EXPECTED_MUSIC_AI


def test_curated_catalog_entries_remain_manual_untrusted_and_fail_closed() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    for item in provider.search(RegistryQuery(technical_only=True, include_deprecated=True)):
        technical = derive_technical_metadata(item)
        assert technical is not None
        assert item.route is DistributionRoute.MANUAL
        assert item.trust_status is TrustStatus.UNTRUSTED
        assert technical.cost_status in {
            "compatible",
            "conditional",
            "incompatible",
            "unknown",
        }
        assert technical.network_status in {"none", "optional", "required", "unknown"}

        if item.item_id in REFERENCE_ONLY:
            assert technical.lifecycle_status == "reference"
            assert technical.evaluation_status == "not-required"
            assert technical.evaluation_required is False
            assert item.deprecated is True
            assert "project-status:archived" in item.tags
        elif item.item_id in DEFERRED_POLICY:
            assert technical.lifecycle_status == "deferred"
            assert technical.evaluation_status == "not-required"
            assert technical.evaluation_required is False
            assert item.deprecated is False
            assert "project-status:not-archived" in item.tags
        else:
            assert technical.lifecycle_status == "candidate"
            assert technical.evaluation_status == "required"
            assert technical.evaluation_required is True

        expected_issue = "502" if item.item_id in ISSUE_502_ITEMS else "638"
        assert item.review_reference == (
            "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/issues/" + expected_issue
        )


def test_curated_catalog_preserves_explicit_unknowns_instead_of_guessing() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    projectatlas = derive_technical_metadata(provider.get("projectatlas"))
    assert projectatlas is not None
    assert projectatlas.deployment_modes == ("unknown",)
    assert projectatlas.cost_status == "unknown"
    assert projectatlas.network_status == "unknown"

    roo = derive_technical_metadata(provider.get("roo-code"))
    assert roo is not None
    assert roo.cost_status == "unknown"
    assert roo.network_status == "unknown"

    for item_id in {"jcode", "paperclip", "openviking", "harbor", "bachi"}:
        metadata = derive_technical_metadata(provider.get(item_id))
        assert metadata is not None
        assert metadata.deployment_modes == ("unknown",)
        assert metadata.cost_status == "unknown"
        assert metadata.network_status == "unknown"
        assert metadata.resource_class == "unknown"


def test_active_external_records_do_not_fake_upstream_revision() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    for item in provider.search(RegistryQuery(technical_only=True, include_deprecated=True)):
        if item.item_id in ISSUE_502_ITEMS | REFERENCE_ONLY:
            continue
        assert item.source.revision is None
        assert "project-status:not-archived" in item.tags
        assert item.deprecated is False


def test_archived_projects_are_reference_only_not_active_candidates() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    for item_id in REFERENCE_ONLY:
        item = provider.get(item_id)
        technical = derive_technical_metadata(item)
        assert technical is not None
        assert item.route is DistributionRoute.MANUAL
        assert item.trust_status is TrustStatus.UNTRUSTED
        assert item.deprecated is True
        assert "project-status:archived" in item.tags
        assert technical.lifecycle_status == "reference"
        assert technical.evaluation_status == "not-required"


def test_policy_or_license_blocked_projects_are_deferred_not_candidates() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    copilot = provider.get("copilot-cli")
    copilot_metadata = derive_technical_metadata(copilot)
    assert copilot_metadata is not None
    assert copilot_metadata.lifecycle_status == "deferred"
    assert copilot_metadata.cost_status == "conditional"
    assert copilot_metadata.network_status == "required"
    assert copilot_metadata.provider_requirements == ("github-copilot",)
    assert "license-restrictions:no-modification" in copilot.tags
    assert "free-tier:available" in copilot.tags
    assert "service-entitlement:required" in copilot.tags

    claude = provider.get("claude-code")
    claude_metadata = derive_technical_metadata(claude)
    assert claude_metadata is not None
    assert claude_metadata.lifecycle_status == "deferred"
    assert claude_metadata.cost_status == "incompatible"
    assert claude_metadata.provider_requirements == ("anthropic",)
    assert "license-restrictions:all-rights-reserved" in claude.tags
    assert "commercial-terms:required" in claude.tags

    musvit = provider.get("musvit")
    musvit_metadata = derive_technical_metadata(musvit)
    assert musvit_metadata is not None
    assert musvit_metadata.lifecycle_status == "deferred"
    assert musvit_metadata.cost_status == "conditional"
    assert musvit_metadata.network_status == "required"
    assert musvit_metadata.resource_class == "gpu"
    assert musvit_metadata.provider_requirements == ("huggingface", "weights-and-biases")
    assert "license-restrictions:non-commercial" in musvit.tags

    legato = provider.get("legato")
    legato_metadata = derive_technical_metadata(legato)
    assert legato_metadata is not None
    assert legato_metadata.lifecycle_status == "deferred"
    assert legato_metadata.cost_status == "conditional"
    assert legato_metadata.network_status == "required"
    assert legato_metadata.resource_class == "gpu"
    assert legato_metadata.provider_requirements == ("huggingface", "meta-llama")
    assert "license-boundary:third-party-model" in legato.tags
    assert "license-restrictions:eu-multimodal-developer-exclusion" in legato.tags
    assert "model-access:gated" in legato.tags


def test_nonstandard_license_terms_are_visible_instead_of_normalized_away() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    dify = provider.get("dify")
    flowise = provider.get("flowise")
    openviking = provider.get("openviking")
    multica = provider.get("multica")
    copilot = provider.get("copilot-cli")
    claude = provider.get("claude-code")
    musvit = provider.get("musvit")

    assert dify.license == "Modified Apache-2.0 (Dify license)"
    assert "license-restrictions:additional-terms" in dify.tags
    assert flowise.license == "Mixed: Apache-2.0 / Commercial"
    assert "license-restrictions:mixed" in flowise.tags
    assert openviking.license == "AGPL-3.0"
    assert "license-review:copyleft" in openviking.tags
    assert multica.license == "Modified Apache-2.0 (Multica License)"
    assert "license-restrictions:hosted-service" in multica.tags
    assert copilot.license == "GitHub Copilot CLI License (restricted)"
    assert "license-restrictions:limited-redistribution" in copilot.tags
    assert claude.license == "Proprietary — Anthropic Commercial Terms"
    assert "license-restrictions:all-rights-reserved" in claude.tags
    assert musvit.license == "CC BY-NC-SA 4.0"
    assert "license-restrictions:share-alike" in musvit.tags


def test_music_model_component_boundaries_are_explicit() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    musicbert = provider.get("musicbert")
    musicbert_metadata = derive_technical_metadata(musicbert)
    assert musicbert_metadata is not None
    assert musicbert.source.repository == "https://github.com/microsoft/muzic"
    assert musicbert.source.package_reference == "github:microsoft/muzic#musicbert"
    assert musicbert.license == "MIT"
    assert musicbert_metadata.lifecycle_status == "candidate"
    assert musicbert_metadata.evaluation_status == "required"
    assert musicbert_metadata.cost_status == "compatible"
    assert musicbert_metadata.network_status == "optional"
    assert musicbert_metadata.resource_class == "gpu"
    assert "scope:code-subproject" in musicbert.tags
    assert "asset-boundary:external-checkpoints" in musicbert.tags
    assert "asset-boundary:external-datasets" in musicbert.tags
    assert "asset-license:unverified" in musicbert.tags

    legato = provider.get("legato")
    legato_metadata = derive_technical_metadata(legato)
    assert legato_metadata is not None
    assert legato.source.repository == "https://github.com/guang-yng/legato"
    assert legato.license == "Mixed: MIT / LGPL-3.0; Llama 3.2 Community License dependency"
    assert legato_metadata.lifecycle_status == "deferred"
    assert legato_metadata.evaluation_status == "not-required"
    assert legato_metadata.cost_status == "conditional"
    assert legato_metadata.network_status == "required"
    assert legato_metadata.resource_class == "gpu"
    assert legato_metadata.provider_requirements == ("huggingface", "meta-llama")
    assert "license-restrictions:mixed" in legato.tags
    assert "model-license:llama-3.2-community" in legato.tags


def test_curated_candidate_artifacts_are_reference_only() -> None:
    provider = FilesystemRegistryProvider(CATALOG)

    for item in provider.search(RegistryQuery(technical_only=True, include_deprecated=True)):
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
    item = registry_item_from_document(_registry_document(schema_version="1"))
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
