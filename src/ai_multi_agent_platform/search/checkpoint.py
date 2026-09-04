"""Checkpoint metadata for derived Search index synchronization (#45)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ai_multi_agent_platform.contracts.types import JsonValue

SEARCH_INDEX_SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class SearchIndexCheckpoint:
    """Provider-reported state for one derived Search index generation.

    Checkpoints are operational metadata only. They never become canonical resource
    state and may be discarded/rebuilt together with the Search index.
    """

    generation: int
    schema_version: str
    document_count: int
    rebuilt_at: str | None
    stale: bool
    stale_reason: str | None = None

    def __post_init__(self) -> None:
        if self.generation < 0:
            raise ValueError("search index generation must not be negative")
        if not self.schema_version.strip():
            raise ValueError("search index schema_version must not be blank")
        if self.document_count < 0:
            raise ValueError("search index document_count must not be negative")
        if self.rebuilt_at is not None:
            try:
                parsed = datetime.fromisoformat(self.rebuilt_at)
            except ValueError as exc:
                raise ValueError("search index rebuilt_at must be an ISO-8601 timestamp") from exc
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("search index rebuilt_at must be timezone-aware")
        if self.stale_reason is not None and not self.stale_reason.strip():
            raise ValueError("search index stale_reason must not be blank")
        if not self.stale and self.stale_reason is not None:
            raise ValueError("fresh search index checkpoint cannot carry stale_reason")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "generation": self.generation,
            "schema_version": self.schema_version,
            "document_count": self.document_count,
            "rebuilt_at": self.rebuilt_at,
            "stale": self.stale,
            "stale_reason": self.stale_reason,
        }
