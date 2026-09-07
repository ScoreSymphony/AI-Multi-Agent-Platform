from __future__ import annotations

import asyncio

from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.contracts.types import (
    HealthStatus,
    JsonValue,
    OperationContext,
    ToolInvocation,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories import RepositoryTree, RepositoryTreeEntry
from ai_multi_agent_platform.repository_intelligence import (
    BaselineRepositoryIntelligenceProvider,
    RepositoryIntelligenceOperation,
    repository_intelligence_capability_specs,
)


_SHA = "a" * 40
_REPOSITORY_ID = new_id("external_resource")


async def _snapshot(
    repository_id: str,
    revision: str,
    context: OperationContext,
) -> RepositoryTree:
    del context
    return RepositoryTree(
        repository_id=repository_id,
        requested_ref=revision,
        resolved_revision=_SHA,
        entries=(
            RepositoryTreeEntry("README.md", b"# Demo\nneedle here\n"),
            RepositoryTreeEntry("src/demo.py", b"def demo():\n    return 'Needle'\n"),
            RepositoryTreeEntry("assets/blob.bin", b"\xff\x00"),
        ),
    )


def _invocation(tool_ref: str, arguments: dict[str, JsonValue]) -> ToolInvocation:
    return ToolInvocation(
        invocation_id=f"issue-502-{tool_ref}",
        tool_ref=tool_ref,
        arguments=arguments,
        context=OperationContext(correlation_id="issue-502"),
    )


def test_baseline_capabilities_are_read_only_and_provider_neutral() -> None:
    specs = {spec.capability_id: spec for spec in repository_intelligence_capability_specs()}

    assert set(specs) == {operation.value for operation in RepositoryIntelligenceOperation}
    for spec in specs.values():
        assert spec.side_effects.value == "none"
        assert spec.required_permissions == (spec.capability_id,)
        assert "provider-neutral" in spec.features
        assert "source-provenance" in spec.features


def test_baseline_map_search_and_source_slice_carry_exact_revision_provenance() -> None:
    async def scenario() -> None:
        provider = BaselineRepositoryIntelligenceProvider(_snapshot)

        mapped = await provider.invoke(
            _invocation(
                "repository.map",
                {"repository_id": _REPOSITORY_ID, "revision": "main"},
            )
        )
        assert isinstance(mapped.output, dict)
        assert mapped.output["returned_entries"] == 3
        assert mapped.output["provenance"] == {
            "repository_id": _REPOSITORY_ID,
            "requested_revision": "main",
            "resolved_revision": _SHA,
            "intelligence_provider_id": "platform.repository-intelligence.baseline",
            "freshness": "live_revision",
        }

        searched = await provider.invoke(
            _invocation(
                "repository.text_search",
                {
                    "repository_id": _REPOSITORY_ID,
                    "revision": "main",
                    "query": "needle",
                    "max_results": 10,
                },
            )
        )
        assert isinstance(searched.output, dict)
        hits = searched.output["hits"]
        assert isinstance(hits, list)
        assert [(hit["path"], hit["line"]) for hit in hits] == [
            ("README.md", 2),
            ("src/demo.py", 2),
        ]
        assert searched.output["skipped_binary_files"] == 1

        sliced = await provider.invoke(
            _invocation(
                "repository.source_slice",
                {
                    "repository_id": _REPOSITORY_ID,
                    "revision": "main",
                    "path": "src/demo.py",
                    "start_line": 2,
                    "end_line": 2,
                },
            )
        )
        assert isinstance(sliced.output, dict)
        assert sliced.output["lines"] == [{"line": 2, "text": "    return 'Needle'"}]
        assert sliced.output["provenance"]["resolved_revision"] == _SHA

    asyncio.run(scenario())


def test_unavailable_high_priority_provider_falls_back_to_baseline() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        baseline = BaselineRepositoryIntelligenceProvider(
            _snapshot,
            provider_id="baseline",
            priority=0,
        )
        optional = BaselineRepositoryIntelligenceProvider(
            _snapshot,
            provider_id="optional-indexer",
            priority=100,
            health=HealthStatus.UNAVAILABLE,
        )
        await registry.register_provider(baseline)
        await registry.register_provider(optional)
        await registry.refresh_health()

        registration, provider = registry.resolve(
            "repository.map",
            granted_permissions=frozenset({"repository.map"}),
        )
        assert registration.provider_id == "baseline"
        assert provider.descriptor.provider_id == "baseline"

    asyncio.run(scenario())


def test_healthy_high_priority_optional_provider_is_preferred_without_becoming_canonical() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        baseline = BaselineRepositoryIntelligenceProvider(
            _snapshot,
            provider_id="baseline",
            priority=0,
        )
        optional = BaselineRepositoryIntelligenceProvider(
            _snapshot,
            provider_id="optional-indexer",
            priority=100,
        )
        await registry.register_provider(baseline)
        await registry.register_provider(optional)
        await registry.refresh_health()

        registration, _provider = registry.resolve(
            "repository.source_slice",
            granted_permissions=frozenset({"repository.source_slice"}),
        )
        assert registration.provider_id == "optional-indexer"

    asyncio.run(scenario())
