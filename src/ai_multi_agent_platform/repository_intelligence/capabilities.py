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

    Multiple providers must publish equivalent stable contract fields for the same
    capability/version so ``CapabilityRegistry`` can safely resolve by health and priority.
    Runtime health/availability are provider state, not capability-contract identity.
    """

    return tuple(_spec(operation) for operation in RepositoryIntelligenceOperation)


def _spec(operation: RepositoryIntelligenceOperation) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=operation.value,
        name=operation.value,
        version="1.0",
        description=_description(operation),
        input_schema=_input_schema(operation),
        output_schema=_output_schema(operation),
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


def _output_schema(operation: RepositoryIntelligenceOperation) -> dict[str, JsonValue]:
    if operation is RepositoryIntelligenceOperation.MAP:
        return _object_schema(
            {
                "entries": {
                    "type": "array",
                    "items": _object_schema(
                        {
                            "path": {"type": "string", "minLength": 1},
                            "size_bytes": {"type": "integer", "minimum": 0},
                        },
                        ("path", "size_bytes"),
                    ),
                },
                "returned_entries": {"type": "integer", "minimum": 0},
                "total_matching_entries": {"type": "integer", "minimum": 0},
                "truncated": {"type": "boolean"},
                "provenance": _provenance_schema(),
            },
            (
                "entries",
                "returned_entries",
                "total_matching_entries",
                "truncated",
                "provenance",
            ),
        )
    if operation is RepositoryIntelligenceOperation.TEXT_SEARCH:
        return _object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "hits": {
                    "type": "array",
                    "items": _object_schema(
                        {
                            "path": {"type": "string", "minLength": 1},
                            "line": {"type": "integer", "minimum": 1},
                            "text": {"type": "string"},
                            "preview_truncated": {"type": "boolean"},
                        },
                        ("path", "line", "text", "preview_truncated"),
                    ),
                },
                "truncated": {"type": "boolean"},
                "skipped_binary_files": {"type": "integer", "minimum": 0},
                "provenance": _provenance_schema(),
            },
            ("query", "hits", "truncated", "skipped_binary_files", "provenance"),
        )
    if operation is RepositoryIntelligenceOperation.SOURCE_SLICE:
        return _object_schema(
            {
                "path": {"type": "string", "minLength": 1},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 0},
                "requested_end_line": {"type": "integer", "minimum": 1},
                "total_lines": {"type": "integer", "minimum": 0},
                "lines": {
                    "type": "array",
                    "items": _object_schema(
                        {
                            "line": {"type": "integer", "minimum": 1},
                            "text": {"type": "string"},
                            "text_truncated": {"type": "boolean"},
                        },
                        ("line", "text", "text_truncated"),
                    ),
                },
                "provenance": _provenance_schema(),
            },
            (
                "path",
                "start_line",
                "end_line",
                "requested_end_line",
                "total_lines",
                "lines",
                "provenance",
            ),
        )
    if operation is RepositoryIntelligenceOperation.HEALTH:
        return _object_schema(
            {
                "provider_id": {"type": "string", "minLength": 1},
                "health": {"type": "string", "minLength": 1},
                "available": {"type": "boolean"},
            },
            ("provider_id", "health", "available"),
        )
    return _object_schema(
        {
            "provider_id": {"type": "string", "minLength": 1},
            "indexed": {"type": "boolean"},
            "state_class": {
                "type": "string",
                "enum": ["derived_index", "authored_metadata", "telemetry"],
            },
            "freshness": {
                "type": "string",
                "enum": ["live_revision", "fresh_index", "stale_index", "unknown"],
            },
            "rebuild_required": {"type": "boolean"},
            "notes": {"type": "string"},
        },
        ("provider_id", "indexed", "state_class", "freshness", "rebuild_required", "notes"),
    )


def _provenance_schema() -> dict[str, JsonValue]:
    return _object_schema(
        {
            "repository_id": {"type": "string", "minLength": 1},
            "requested_revision": {"type": "string", "minLength": 1},
            "resolved_revision": {"type": "string", "minLength": 1},
            "intelligence_provider_id": {"type": "string", "minLength": 1},
            "freshness": {
                "type": "string",
                "enum": ["live_revision", "fresh_index", "stale_index", "unknown"],
            },
        },
        (
            "repository_id",
            "requested_revision",
            "resolved_revision",
            "intelligence_provider_id",
            "freshness",
        ),
    )


def _object_schema(
    properties: dict[str, JsonValue],
    required: tuple[str, ...],
) -> dict[str, JsonValue]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": True,
    }
