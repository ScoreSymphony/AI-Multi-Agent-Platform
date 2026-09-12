from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.search import (
    SEARCH_INDEX_SCHEMA_VERSION,
    LocalSearchProvider,
    SearchDocument,
)


def _context() -> OperationContext:
    return OperationContext(correlation_id="issue-45-checkpoint")


def test_local_provider_checkpoint_tracks_generation_freshness_and_document_count() -> None:
    async def scenario() -> None:
        provider = LocalSearchProvider()
        context = _context()
        first_id = new_id("task")
        second_id = new_id("task")
        first = SearchDocument(resource_type="task", resource_id=first_id, title="First")
        second = SearchDocument(resource_type="task", resource_id=second_id, title="Second")

        assert await provider.index_checkpoint(context) is None

        await provider.upsert(first, context)
        checkpoint = await provider.index_checkpoint(context)
        assert checkpoint is not None
        assert checkpoint.generation == 1
        assert checkpoint.schema_version == SEARCH_INDEX_SCHEMA_VERSION
        assert checkpoint.document_count == 1
        assert checkpoint.rebuilt_at is None
        assert checkpoint.stale is True
        assert checkpoint.stale_reason == "incremental update before full rebuild"

        await provider.mark_stale("missed canonical event", context)
        stale = await provider.index_checkpoint(context)
        assert stale is not None
        assert stale.generation == 1
        assert stale.stale is True
        assert stale.stale_reason == "missed canonical event"

        await provider.upsert(second, context)
        still_stale = await provider.index_checkpoint(context)
        assert still_stale is not None
        assert still_stale.generation == 2
        assert still_stale.document_count == 2
        assert still_stale.stale is True
        assert still_stale.stale_reason == "missed canonical event"

        await provider.rebuild((first, second), context)
        rebuilt = await provider.index_checkpoint(context)
        assert rebuilt is not None
        assert rebuilt.generation == 3
        assert rebuilt.document_count == 2
        assert rebuilt.rebuilt_at is not None
        assert rebuilt.stale is False
        assert rebuilt.stale_reason is None
        assert rebuilt.to_json()["schema_version"] == SEARCH_INDEX_SCHEMA_VERSION

        await provider.delete(second.resource_type, second.resource_id, context)
        after_delete = await provider.index_checkpoint(context)
        assert after_delete is not None
        assert after_delete.generation == 4
        assert after_delete.document_count == 1
        assert after_delete.stale is False

    asyncio.run(scenario())
