"""Canonical, backend-neutral search value types for issue #45."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id


class SearchMode(StrEnum):
    """Search modes understood by the canonical query contract."""

    EXACT = "exact"
    KEYWORD = "keyword"
    METADATA = "metadata"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Backend-neutral query passed to a replaceable SearchProvider.

    Semantic and hybrid modes are canonical vocabulary but optional provider
    capabilities. The dependency-free baseline implements exact, keyword and
    metadata filtering only.
    """

    text: str | None = None
    exact_id: str | None = None
    resource_types: tuple[str, ...] = ()
    project_id: str | None = None
    workspace_id: str | None = None
    statuses: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    source_filters: tuple[str, ...] = ()
    provider_filters: tuple[str, ...] = ()
    mode: SearchMode = SearchMode.KEYWORD
    limit: int = 50
    cursor: str | None = None
    sort: Literal["relevance", "id", "updated_at"] = "relevance"
    direction: Literal["asc", "desc"] = "desc"

    def __post_init__(self) -> None:
        if self.text is not None and not self.text.strip():
            raise ValueError("text must not be blank")
        if self.exact_id is not None and not self.exact_id.strip():
            raise ValueError("exact_id must not be blank")
        if not 1 <= self.limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.workspace_id is not None:
            validate_id(self.workspace_id, "workspace")
        for values, name in (
            (self.resource_types, "resource_types"),
            (self.statuses, "statuses"),
            (self.tags, "tags"),
            (self.source_filters, "source_filters"),
            (self.provider_filters, "provider_filters"),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"{name} must not contain blank values")
        if self.mode in {SearchMode.SEMANTIC, SearchMode.HYBRID} and self.text is None:
            raise ValueError(f"{self.mode.value} search requires text")


@dataclass(frozen=True, slots=True)
class SearchDocument:
    """Derived index document. It is never canonical platform state."""

    resource_type: str
    resource_id: str
    title: str
    summary: str = ""
    project_id: str | None = None
    workspace_id: str | None = None
    status: str | None = None
    tags: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    source: str = "canonical"
    provider: str = "control-plane"
    version: str | None = None
    updated_at: str | None = None
    canonical_ref: str | None = None
    provenance: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.resource_type.strip():
            raise ValueError("resource_type must not be blank")
        if not self.resource_id.strip():
            raise ValueError("resource_id must not be blank")
        if not self.title.strip():
            raise ValueError("title must not be blank")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.workspace_id is not None:
            validate_id(self.workspace_id, "workspace")

    @property
    def key(self) -> tuple[str, str]:
        return self.resource_type, self.resource_id


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One safe canonical discovery result."""

    resource_type: str
    resource_id: str
    title: str
    summary: str
    project_id: str | None
    workspace_id: str | None
    status: str | None
    tags: tuple[str, ...]
    relevance: float
    matched_fields: tuple[str, ...]
    source: str
    provider: str
    version: str | None
    updated_at: str | None
    canonical_ref: str | None
    provenance: dict[str, JsonValue]

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "title": self.title,
            "summary": self.summary,
            "project_id": self.project_id,
            "workspace_id": self.workspace_id,
            "status": self.status,
            "tags": list(self.tags),
            "relevance": self.relevance,
            "matched_fields": list(self.matched_fields),
            "source": self.source,
            "provider": self.provider,
            "version": self.version,
            "updated_at": self.updated_at,
            "canonical_ref": self.canonical_ref,
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class SearchPage:
    items: tuple[SearchResult, ...]
    total: int
    limit: int
    next_cursor: str | None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "items": [item.to_json() for item in self.items],
            "total": self.total,
            "limit": self.limit,
            "next_cursor": self.next_cursor,
        }


def encode_search_cursor(offset: int) -> str:
    if offset < 0:
        raise ValueError("offset must not be negative")
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii").rstrip("=")


def decode_search_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode((cursor + padding).encode("ascii")).decode("ascii")
        offset = int(decoded)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("search cursor is invalid") from exc
    if offset < 0:
        raise ValueError("search cursor is invalid")
    return offset
