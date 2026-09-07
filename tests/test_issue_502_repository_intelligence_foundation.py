from __future__ import annotations

import asyncio

from jsonschema import Draft202012Validator

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


def test_output_schemas_require_provider_neutral_result_contracts() -> None:
    specs = {spec.capability_id: spec for spec in repository_intelligence_capability_specs()}
    required = {
        "repository.map": {
            "entries",
            "returned_entries",
            "total_matching_entries",
            "truncated",
            "provenance",
        },
        "repository.text_search": {
            "query",
            "hits",
            "truncated",
            "skipped_binary_files",
            "provenance",
        },
        "repository.source_slice": {
            "path",
            "start_line",
            "end_line",
            "requested_end_line",
            "total_lines",
            "lines",
            "truncated",
            "provenance",
        },
        "repository.health": {"provider_id", "health", "available"},
        "repository.index_status": {
            "provider_id",
            "indexed",
            "state_class",
            "freshness",
            "rebuild_required",
            "notes",
        },
    }

    for capability_id, expected_required in required.items():
        schema = specs[capability_id].output_schema
        assert schema is not None
        schema_required = schema.get("required")
        assert isinstance(schema_required, list)
        assert set(schema_required) == expected_required

    map_schema = specs["repository.map"].output_schema
    assert map_schema is not None
    validator = Draft202012Validator(map_schema)
    invalid_without_provenance = {
        "entries": [],
        "returned_entries": 0,
        "total_matching_entries": 0,
        "truncated": False,
    }
    assert list(validator.iter_errors(invalid_without_provenance))

    invalid_mutable_revision = {
        "entries": [],
        "returned_entries": 0,
        "total_matching_entries": 0,
        "truncated": False,
        "provenance": {
            "repository_id": _REPOSITORY_ID,
            "requested_revision": "main",
            "resolved_revision": "main",
            "intelligence_provider_id": "optional-indexer",
            "freshness": "fresh_index",
        },
    }
    assert list(validator.iter_errors(invalid_mutable_revision))


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
        assert sliced.output["lines"] == [
            {"line": 2, "text": "    return 'Needle'", "text_truncated": False}
        ]
        assert sliced.output["truncated"] is False
        provenance = sliced.output["provenance"]
        assert isinstance(provenance, dict)
        assert provenance["resolved_revision"] == _SHA

    asyncio.run(scenario())


def test_text_search_reports_truncation_only_when_a_match_is_omitted() -> None:
    async def exact_loader(
        repository_id: str,
        revision: str,
        context: OperationContext,
    ) -> RepositoryTree:
        del context
        return RepositoryTree(
            repository_id=repository_id,
            requested_ref=revision,
            resolved_revision=_SHA,
            entries=(RepositoryTreeEntry("one.txt", b"needle\n"),),
        )

    async def overflow_loader(
        repository_id: str,
        revision: str,
        context: OperationContext,
    ) -> RepositoryTree:
        del context
        return RepositoryTree(
            repository_id=repository_id,
            requested_ref=revision,
            resolved_revision=_SHA,
            entries=(RepositoryTreeEntry("two.txt", b"needle\nneedle again\n"),),
        )

    async def scenario() -> None:
        arguments: dict[str, JsonValue] = {
            "repository_id": _REPOSITORY_ID,
            "query": "needle",
            "max_results": 1,
        }
        exact = await BaselineRepositoryIntelligenceProvider(exact_loader).invoke(
            _invocation("repository.text_search", arguments)
        )
        overflow = await BaselineRepositoryIntelligenceProvider(overflow_loader).invoke(
            _invocation("repository.text_search", arguments)
        )

        assert isinstance(exact.output, dict)
        assert exact.output["truncated"] is False
        assert isinstance(overflow.output, dict)
        assert overflow.output["truncated"] is True
        overflow_hits = overflow.output["hits"]
        assert isinstance(overflow_hits, list)
        assert len(overflow_hits) == 1

    asyncio.run(scenario())


def test_source_slice_bounds_oversized_lines_and_total_output() -> None:
    async def long_line_loader(
        repository_id: str,
        revision: str,
        context: OperationContext,
    ) -> RepositoryTree:
        del context
        huge_line = ("x" * 100_000).encode()
        return RepositoryTree(
            repository_id=repository_id,
            requested_ref=revision,
            resolved_revision=_SHA,
            entries=(RepositoryTreeEntry("minified.js", huge_line),),
        )

    async def scenario() -> None:
        sliced = await BaselineRepositoryIntelligenceProvider(long_line_loader).invoke(
            _invocation(
                "repository.source_slice",
                {
                    "repository_id": _REPOSITORY_ID,
                    "path": "minified.js",
                    "start_line": 1,
                    "end_line": 1,
                },
            )
        )
        assert isinstance(sliced.output, dict)
        lines = sliced.output["lines"]
        assert isinstance(lines, list)
        assert len(lines) == 1
        line = lines[0]
        assert isinstance(line, dict)
        text = line["text"]
        assert isinstance(text, str)
        assert len(text) == 4096
        assert line["text_truncated"] is True
        assert sliced.output["truncated"] is True

    asyncio.run(scenario())


def test_unavailable_high_priority_provider_falls_back_immediately() -> None:
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
        await registry.refresh_health()
        await registry.register_provider(optional)

        registration, provider = registry.resolve(
            "repository.map",
            granted_permissions=frozenset({"repository.map"}),
        )
        assert registration.provider_id == "baseline"
        assert provider.descriptor.provider_id == "baseline"

    asyncio.run(scenario())


def test_unavailable_by_configuration_provider_falls_back_immediately() -> None:
    async def scenario() -> None:
        registry = CapabilityRegistry()
        baseline = BaselineRepositoryIntelligenceProvider(
            _snapshot,
            provider_id="baseline",
            priority=0,
        )
        optional = BaselineRepositoryIntelligenceProvider(
            _snapshot,
            provider_id="disabled-indexer",
            priority=100,
            available=False,
        )
        await registry.register_provider(baseline)
        await registry.register_provider(optional)

        registration, _provider = registry.resolve(
            "repository.text_search",
            granted_permissions=frozenset({"repository.text_search"}),
        )
        assert registration.provider_id == "baseline"

    asyncio.run(scenario())


def test_provider_can_register_after_health_refresh_and_be_preferred_immediately() -> None:
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
        await registry.refresh_health()
        await registry.register_provider(optional)

        registration, _provider = registry.resolve(
            "repository.source_slice",
            granted_permissions=frozenset({"repository.source_slice"}),
        )
        assert registration.provider_id == "optional-indexer"

    asyncio.run(scenario())
