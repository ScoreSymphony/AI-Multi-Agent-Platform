"""Composition helpers for multiple canonical observability timeline readers."""

from __future__ import annotations

from typing import Protocol, cast

from .integrations import TimelineReader
from .models import TimelineEntry


class _AsyncTimelineReader(Protocol):
    async def query_timeline_async(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[TimelineEntry, ...]: ...


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

    async def query_timeline_async(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[TimelineEntry, ...]:
        entries: list[TimelineEntry] = []
        for reader in self._readers:
            if hasattr(reader, "query_timeline_async"):
                async_reader = cast(_AsyncTimelineReader, reader)
                projected = await async_reader.query_timeline_async(
                    task_id=task_id,
                    run_id=run_id,
                    correlation_id=correlation_id,
                )
            else:
                projected = reader.query_timeline(
                    task_id=task_id,
                    run_id=run_id,
                    correlation_id=correlation_id,
                )
            entries.extend(projected)
        return tuple(sorted(entries, key=lambda entry: entry.timestamp))
