from __future__ import annotations

import asyncio
import weakref
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.research import (
    AsyncResearchRepositoryAdapter,
    InMemoryResearchRepository,
    ResearchClass,
    ResearchItem,
    ResearchSourceType,
    SourceObservation,
    SourceRecord,
)
from ai_multi_agent_platform.research.repository import ResearchRepository

_OWNER = OwnerRef(type="user", id="research-async-hardening")


def _item() -> ResearchItem:
    return ResearchItem(
        title="Research async hardening",
        question="Does the async adapter preserve repository contracts?",
        research_class=ResearchClass.PROJECT_RESEARCH,
        owner_ref=_OWNER,
    )


class _OpaqueResearchRepository:
    """Protocol-compatible repository that is unhashable and cannot be weak-referenced."""

    __slots__ = ("_inner",)
    __hash__ = None

    def __init__(self) -> None:
        self._inner = InMemoryResearchRepository()

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._inner, name)


def test_research_adapter_supports_unhashable_nonweakref_repository() -> None:
    repository = _OpaqueResearchRepository()
    with pytest.raises(TypeError):
        weakref.ref(repository)

    adapter = AsyncResearchRepositoryAdapter(cast(ResearchRepository, repository))
    item = _item()

    async def scenario() -> None:
        assert await adapter.create_item(item) == item
        assert await adapter.get_item(item.research_item_id) == item

    asyncio.run(scenario())


def test_concurrent_stale_source_updates_merge_observation_history() -> None:
    repository = InMemoryResearchRepository()
    first = AsyncResearchRepositoryAdapter(repository)
    second = AsyncResearchRepositoryAdapter(repository)
    item = _item()
    source = SourceRecord(
        research_item_id=item.research_item_id,
        source_type=ResearchSourceType.WEB,
        locator="https://example.invalid/research",
        title="Concurrent source",
    )

    async def scenario() -> None:
        await first.create_item(item)
        await first.create_source(source)
        stale = await first.get_source(source.source_id)
        first_observation = SourceObservation(
            source_id=source.source_id,
            research_item_id=item.research_item_id,
            retrieved_at=datetime.now(UTC),
        )
        second_observation = SourceObservation(
            source_id=source.source_id,
            research_item_id=item.research_item_id,
            retrieved_at=datetime.now(UTC),
        )
        await asyncio.gather(
            first.create_observation(first_observation),
            second.create_observation(second_observation),
        )

        first_update = replace(
            stale,
            current_observation_id=first_observation.observation_id,
            observation_ids=(first_observation.observation_id,),
        )
        second_update = replace(
            stale,
            current_observation_id=second_observation.observation_id,
            observation_ids=(second_observation.observation_id,),
        )
        await asyncio.gather(
            first.save_source(first_update),
            second.save_source(second_update),
        )

        stored = await first.get_source(source.source_id)
        assert set(stored.observation_ids) == {
            first_observation.observation_id,
            second_observation.observation_id,
        }
        assert len(stored.observation_ids) == 2

    asyncio.run(scenario())
