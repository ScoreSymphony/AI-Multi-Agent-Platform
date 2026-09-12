from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
    new_file_id,
    new_knowledge_source_id,
    new_memory_id,
)
from ai_multi_agent_platform.domain import new_id


def _operation(project_id: str | None = None, owner_id: str = "user-a") -> OperationContext:
    return OperationContext(
        correlation_id="corr-hardening",
        owner_type="user",
        owner_id=owner_id,
        project_id=project_id,
    )


def _context(project_id: str | None = None, owner_id: str = "user-a") -> DataAccessContext:
    return DataAccessContext(
        operation=_operation(project_id, owner_id),
        actor_ref=f"user:{owner_id}",
    )


def test_local_provider_maps_backend_failure_to_canonical_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = LocalMemoryProvider(tmp_path / "data.sqlite")

    def fail_connect() -> sqlite3.Connection:
        raise sqlite3.OperationalError("simulated backend outage")

    monkeypatch.setattr(provider, "_connect", fail_connect)
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(provider.get_entry(new_memory_id(), _context()))
    assert exc_info.value.code is ErrorCode.BACKEND_ERROR


def test_missing_file_and_knowledge_references_map_to_not_found(tmp_path: Path) -> None:
    context = _context(new_id("project"))
    files = LocalFileProvider(tmp_path / "files", tmp_path / "data.sqlite")
    knowledge = LocalKnowledgeProvider(tmp_path / "data.sqlite")

    with pytest.raises(ContractError) as file_exc:
        asyncio.run(files.get_file(new_file_id(), context))
    assert file_exc.value.code is ErrorCode.NOT_FOUND

    with pytest.raises(ContractError) as knowledge_exc:
        asyncio.run(knowledge.get_index_status(new_knowledge_source_id(), context))
    assert knowledge_exc.value.code is ErrorCode.NOT_FOUND
