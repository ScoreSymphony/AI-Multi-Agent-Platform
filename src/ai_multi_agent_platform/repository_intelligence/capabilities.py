"""Canonical repository-intelligence capability taxonomy for issue #502."""

from __future__ import annotations

from enum import StrEnum

from ai_multi_agent_platform.capabilities import (
    CapabilitySpec,
    SafetyClassification,
    SideEffectClassification,
)
from ai_multi_agent_platform.contracts.types import JsonValue


class RepositoryIntelligenceOperation(StrEnum):
    """Provider-neutral repository-intelligence operations.

    The deterministic baseline intentionally exposes only operations it can answer from an
    exact canonical repository tree. Richer symbol/graph/semantic capabilities may be added by
    optional providers without making those providers platform dependencies.
    """

    MAP = "repository.map"
    TEXT_SEARCH = "repository.text_search"
    SOURCE_SLICE = "repository.source_slice"
    HEALTH = "repository.health"
    INDEX_STATUS = "repository.index_status"


def repository_intelligence_capability_specs() -> tuple[CapabilitySpec, ...]:
    """Return canonical #12-compatible capability definitions.

    Multiple providers must publish byte-for-byte equivalent ``CapabilitySpec`` values for the
    same capability/version so ``CapabilityRegistry`` can safely resolve by health and priority.
    """

    return tuple(_spec(operation) for operation in RepositoryIntelligenceOperation)


def _spec(operation: RepositoryIntelligenceOperation) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=operation.value,
        name=operation.value,
        version="1.0",
        description=_description(operation),
        input_schema=_input_schema(operation),
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
        },
        tags=("repository", "code-intelligence", "read-only"),
        safety=SafetyClassification.STANDARD,
        side_effects=SideEffectClassification.NONE,
        required_permissions=(operation.value,),
        features=("provider-neutral", "source-provenance"),
    )


def _description(operation: RepositoryIntelligenceOperation) -> str:
    descriptions = {
        RepositoryIntelligenceOperation.MAP: (
            "List a bounded map of files from an exact canonical repository revision"
        ),
        RepositoryIntelligenceOperation.TEXT_SEARCH: (
            "Search text deterministically in an exact canonical repository revision"
        ),
        RepositoryIntelligenceOperation.SOURCE_SLICE: (
            "Read an exact bounded source slice with revision provenance"
        ),
        RepositoryIntelligenceOperation.HEALTH: (
            "Read repository-intelligence provider health metadata"
        ),
        RepositoryIntelligenceOperation.INDEX_STATUS: (
            "Read provider index/freshness state without treating it as canonical repository truth"
        ),
    }
    return descriptions[operation]


def _input_schema(operation: RepositoryIntelligenceOperation) -> dict[str, JsonValue]:
    properties: dict[str, JsonValue] = {}
    required: list[JsonValue] = []

    if operation in {
        RepositoryIntelligenceOperation.MAP,
        RepositoryIntelligenceOperation.TEXT_SEARCH,
        RepositoryIntelligenceOperation.SOURCE_SLICE,
    }:
        properties.update(
            {
                "repository_id": {"type": "string", "minLength": 1},
                "revision": {"type": "string", "minLength": 1},
            }
        )
        required.append("repository_id")

    if operation is RepositoryIntelligenceOperation.MAP:
        properties.update(
            {
                "path_prefix": {"type": "string", "minLength": 1},
                "max_entries": {"type": "integer", "minimum": 1, "maximum": 5000},
            }
        )
    elif operation is RepositoryIntelligenceOperation.TEXT_SEARCH:
        properties.update(
            {
                "query": {"type": "string", "minLength": 1},
                "path_prefix": {"type": "string", "minLength": 1},
                "case_sensitive": {"type": "boolean"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
            }
        )
        required.append("query")
    elif operation is RepositoryIntelligenceOperation.SOURCE_SLICE:
        properties.update(
            {
                "path": {"type": "string", "minLength": 1},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
            }
        )
        required.append("path")

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
