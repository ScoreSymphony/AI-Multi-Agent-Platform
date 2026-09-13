from __future__ import annotations

import asyncio
from pathlib import Path

from data_lifecycle_command_cases import _providers, _request_context

from ai_multi_agent_platform.data.lifecycle_commands import data_command_handlers
from ai_multi_agent_platform.domain import new_id


def test_knowledge_register_update_ingest_reindex_detach_lifecycle(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    project_id = new_id("project")
    handlers = data_command_handlers(providers, project_ids=lambda: (project_id,))
    context = _request_context()

    registered = asyncio.run(
        handlers["knowledge.register"](
            context,
            project_id,
            {"project_id": project_id, "title": "Issue 251 knowledge", "revision": "r1"},
        )
    )
    source_id = registered["id"]
    assert isinstance(source_id, str)
    assert registered["status"] == "registered"

    updated = asyncio.run(
        handlers["knowledge.update"](
            context,
            source_id,
            {"title": "Issue 251 knowledge updated", "metadata": {"kind": "reference"}},
        )
    )
    assert updated["id"] == source_id
    assert updated["title"] == "Issue 251 knowledge updated"
    assert updated["revision"] == "r1"
    assert updated["metadata"] == {"kind": "reference"}

    ingested = asyncio.run(
        handlers["knowledge.ingest"](
            context,
            source_id,
            {"content": "canonical source-backed knowledge", "location": "section:one"},
        )
    )
    assert ingested["source_id"] == source_id
    assert ingested["revision"] == "r1"

    reindexed = asyncio.run(
        handlers["knowledge.reindex"](
            context,
            source_id,
            {
                "revision": "r2",
                "content": "updated canonical knowledge",
                "location": "section:two",
            },
        )
    )
    assert reindexed["source_id"] == source_id
    assert reindexed["revision"] == "r2"

    detached = asyncio.run(handlers["knowledge.detach"](context, source_id, {}))
    assert detached["id"] == source_id
    assert detached["status"] == "removed"
    assert detached["detached"] is True


def test_knowledge_delete_preserves_canonical_source_tombstone(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    project_id = new_id("project")
    handlers = data_command_handlers(providers, project_ids=lambda: (project_id,))
    context = _request_context()
    registered = asyncio.run(
        handlers["knowledge.register"](
            context,
            project_id,
            {"project_id": project_id, "title": "Delete lifecycle", "revision": "r1"},
        )
    )
    source_id = registered["id"]
    assert isinstance(source_id, str)

    deleted = asyncio.run(handlers["knowledge.delete"](context, source_id, {}))
    assert deleted["id"] == source_id
    assert deleted["status"] == "removed"
    assert deleted["deleted"] is True
