from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    KnowledgeSource,
    KnowledgeStatus,
    LocalKnowledgeProvider,
    new_knowledge_source_id,
)
from ai_multi_agent_platform.domain import new_id


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="corr-index-stability",
            owner_type="user",
            owner_id="user-a",
            project_id=project_id,
        ),
        actor_ref="user:user-a",
    )


def test_knowledge_index_id_is_stable_from_registration_through_reindex(
    tmp_path: Path,
) -> None:
    project_id = new_id("project")
    context = _context(project_id)
    provider = LocalKnowledgeProvider(tmp_path / "data.sqlite")
    now = datetime.now(UTC)
    source = KnowledgeSource(
        source_id=new_knowledge_source_id(),
        project_id=project_id,
        owner_ref="user:user-a",
        created_by="user:user-a",
        title="Stable index identity",
        revision="r1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
    )

    asyncio.run(provider.register_source(source, context))
    registered_once = asyncio.run(provider.get_index_status(source.source_id, context))
    registered_twice = asyncio.run(provider.get_index_status(source.source_id, context))

    assert registered_once.index_id.startswith("knowledge_index_")
    assert registered_twice.index_id == registered_once.index_id
    assert registered_once.status is KnowledgeStatus.REGISTERED

    asyncio.run(provider.ingest_source(source.source_id, "first revision", "line:1", context))
    ready = asyncio.run(provider.get_index_status(source.source_id, context))
    assert ready.index_id == registered_once.index_id
    assert ready.revision == "r1"
    assert ready.status is KnowledgeStatus.READY

    asyncio.run(
        provider.reindex_source(
            source.source_id,
            "r2",
            "second revision",
            "line:2",
            context,
        )
    )
    reindexed = asyncio.run(provider.get_index_status(source.source_id, context))
    assert reindexed.index_id == registered_once.index_id
    assert reindexed.revision == "r2"
    assert reindexed.status is KnowledgeStatus.READY
