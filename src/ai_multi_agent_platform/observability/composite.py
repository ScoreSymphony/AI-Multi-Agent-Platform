"""Composition helpers for multiple canonical observability timeline readers."""

from __future__ import annotations

from .integrations import TimelineReader
from .models import TimelineEntry


class CompositeTimelineReader:
    """Merge multiple derived timeline sources without changing lifecycle authority."""

    def __init__(self, readers: tuple[TimelineReader, ...]) -> None:
        self._readers = readers

    def query_timeline(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[TimelineEntry, ...]:
        entries = [
            entry
            for reader in self._readers
            for entry in reader.query_timeline(
                task_id=task_id,
                run_id=run_id,
                correlation_id=correlation_id,
            )
        ]
        return tuple(sorted(entries, key=lambda entry: entry.timestamp))
