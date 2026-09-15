from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import cast

from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories import (
    ASYNC_REPOSITORY_PROVENANCE_BACKEND_MARKER,
    AsyncRepositoryProvenanceStore,
    RepositoryRunProvenance,
    as_async_repository_provenance_store,
)


async def _completed[T](value: T) -> T:
    return value


class _MarkedAwaitableProvenanceStore:
    __ai_multi_agent_async_repository_provenance__ = True

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], RepositoryRunProvenance] = {}

    def record(self, provenance: RepositoryRunProvenance) -> Awaitable[None]:
        self._records[(provenance.run_id, provenance.repository_id)] = provenance
        return _completed(None)

    def upsert(self, provenance: RepositoryRunProvenance) -> Awaitable[None]:
        self._records[(provenance.run_id, provenance.repository_id)] = provenance
        return _completed(None)

    def get(
        self,
        run_id: str,
        repository_id: str,
    ) -> Awaitable[RepositoryRunProvenance | None]:
        return _completed(self._records.get((run_id, repository_id)))

    def for_run(self, run_id: str) -> Awaitable[tuple[RepositoryRunProvenance, ...]]:
        return _completed(
            tuple(record for (record_run_id, _), record in self._records.items() if record_run_id == run_id)
        )


def test_explicit_marker_preserves_regular_def_awaitable_backend() -> None:
    store = _MarkedAwaitableProvenanceStore()
    provenance = RepositoryRunProvenance(
        run_id=new_id("run"),
        repository_id=new_id("external_resource"),
        input_revision="0" * 40,
        actor_ref="user:provenance-marker",
    )

    assert getattr(store, ASYNC_REPOSITORY_PROVENANCE_BACKEND_MARKER) is True
    resolved = as_async_repository_provenance_store(
        cast(AsyncRepositoryProvenanceStore, store)
    )
    assert resolved is store

    async def exercise() -> None:
        await resolved.record(provenance)
        assert await resolved.get(provenance.run_id, provenance.repository_id) == provenance
        assert await resolved.for_run(provenance.run_id) == (provenance,)

    asyncio.run(exercise())
