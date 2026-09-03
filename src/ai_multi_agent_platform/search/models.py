"""Canonical, backend-neutral search value types for issue #45."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id

_TASK_PRIORITIES = frozenset({"low", "normal", "high", "urgent"})


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
    updated_after: datetime | None = None
    updated_before: datetime | None = None
    priorities: tuple[str, ...] = ()
    due_after: datetime | None = None
    due_before: datetime | None = None
    assignment_state: Literal["assigned", "unassigned"] | None = None
    responsible_id: str | None = None
    agent_assignment_id: str | None = None
    blocked: bool | None = None
    overdue: bool | None = None
    dependency_id: str | None = None
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
        if self.mode is SearchMode.EXACT and self.exact_id is None:
            raise ValueError("exact search requires exact_id")
        if not 1 <= self.limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.workspace_id is not None:
            validate_id(self.workspace_id, "workspace")
        if self.dependency_id is not None:
            validate_id(self.dependency_id, "task")
        for values, name in (
            (self.resource_types, "resource_types"),
            (self.statuses, "statuses"),
            (self.tags, "tags"),
            (self.source_filters, "source_filters"),
            (self.provider_filters, "provider_filters"),
            (self.priorities, "priorities"),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"{name} must not contain blank values")
        if any(priority not in _TASK_PRIORITIES for priority in self.priorities):
            raise ValueError("priorities must contain only low, normal, high or urgent")
        for value, name in (
            (self.responsible_id, "responsible_id"),
            (self.agent_assignment_id, "agent_assignment_id"),
        ):
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank")
        for value, name in (
            (self.updated_after, "updated_after"),
            (self.updated_before, "updated_before"),
            (self.due_after, "due_after"),
            (self.due_before, "due_before"),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must be timezone-aware")
        if (
            self.updated_after is not None
            and self.updated_before is not None
            and self.updated_after > self.updated_before
        ):
            raise ValueError("updated_after must not be later than updated_before")
        if (
            self.due_after is not None
            and self.due_before is not None
            and self.due_after > self.due_before
        ):
            raise ValueError("due_after must not be later than due_before")
        if self.assignment_state not in {None, "assigned", "unassigned"}:
            raise ValueError("assignment_state must be assigned or unassigned")
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
    owner_type: str | None = None
    owner_id: str | None = None
    status: str | None = None
    tags: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    source: str = "canonical"
    provider: str = "control-plane"
    version: str | None = None
    updated_at: str | None = None
    canonical_ref: str | None = None
    provenance: dict[str, JsonValue] = field(default_factory=dict)
    priority: str | None = None
    due_at: str | None = None
    responsible_id: str | None = None
    agent_assignment_id: str | None = None
    blocked: bool | None = None
    overdue: bool | None = None
    dependency_ids: tuple[str, ...] = ()

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
        if (self.owner_type is None) != (self.owner_id is None):
            raise ValueError("owner_type and owner_id must both be set or both be omitted")
        if self.updated_at is not None:
            parsed = _parse_timestamp(self.updated_at, "updated_at")
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("updated_at must be timezone-aware")
        if self.due_at is not None:
            parsed = _parse_timestamp(self.due_at, "due_at")
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("due_at must be timezone-aware")
        for dependency_id in self.dependency_ids:
            validate_id(dependency_id, "task")

    @property
    def key(self) -> tuple[str, str]:
        return self.resource_type, self.resource_id

    @property
    def updated_at_datetime(self) -> datetime | None:
        if self.updated_at is None:
            return None
        return _parse_timestamp(self.updated_at, "updated_at")

    @property
    def due_at_datetime(self) -> datetime | None:
        if self.due_at is None:
            return None
        return _parse_timestamp(self.due_at, "due_at")


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One canonical discovery result with explicit authorization state."""

    resource_type: str
    resource_id: str
    title: str
    summary: str
    project_id: str | None
    workspace_id: str | None
    owner_type: str | None
    owner_id: str | None
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
    access: Literal["unverified", "authorized"] = "unverified"
    redacted: bool = False

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "title": self.title,
            "summary": self.summary,
            "project_id": self.project_id,
            "workspace_id": self.workspace_id,
            "owner_type": self.owner_type,
            "owner_id": self.owner_id,
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
            "access": self.access,
            "redacted": self.redacted,
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


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
