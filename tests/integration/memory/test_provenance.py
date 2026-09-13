from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryOrigin,
    MemoryScope,
    RetentionPolicy,
    SourceRef,
    new_memory_id,
)
from ai_multi_agent_platform.data.control_plane import MemoryResourceService
from ai_multi_agent_platform.search import document_from_resource


def _context() -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-290-provenance",
            owner_type="user",
            owner_id="alice",
        ),
        actor_ref="user:alice",
    )


def test_memory_search_document_preserves_refs_without_private_provenance_details(tmp_path) -> None:
    async def scenario() -> None:
        provider = LocalMemoryProvider(tmp_path / "memory.sqlite3")
        source_memory_id = new_memory_id()
        entry = MemoryEntry(
            memory_id=new_memory_id(),
            scope=MemoryScope.USER,
            scope_id="alice",
            owner_ref="user:alice",
            created_by="user:alice",
            value={"private": "must-not-enter-search"},
            created_at=datetime.now(UTC),
            retention=RetentionPolicy.DURABLE,
            origin=MemoryOrigin.IMPORTED,
            provenance=(
                SourceRef(
                    kind="memory",
                    ref=source_memory_id,
                    location="/srv/private/provider/path",
                    checksum="a" * 64,
                ),
            ),
            metadata={"provider_index_id": "private-provider-index"},
        )
        await provider.write_entry(entry, _context())

        resources = await MemoryResourceService(provider).list_search_resources()
        assert len(resources) == 1
        resource = resources[0]
        assert resource["provenance_refs"] == [source_memory_id]
        assert "value" not in resource
        assert "metadata" not in resource

        document = document_from_resource(resource, collection="memory")
        assert document.provenance["canonical_refs"] == [source_memory_id]
        assert "private-provider-index" not in str(document)
        assert "/srv/private/provider/path" not in str(document)
        assert "a" * 64 not in str(document)

    asyncio.run(scenario())
