from __future__ import annotations

from pathlib import Path

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    DataProviderSet,
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
)


def _request_context() -> RequestContext:
    return RequestContext(
        request_id="request-251-command",
        correlation_id="correlation-251-command",
        actor=ActorContext(
            principal_ref="user:user-a",
            owner_type="user",
            owner_id="user-a",
        ),
        idempotency_key="idem-251-command",
    )


def _data_context() -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="correlation-251-command-data",
            owner_type="user",
            owner_id="user-a",
        ),
        actor_ref="user:user-a",
    )


def _providers(tmp_path: Path) -> DataProviderSet:
    return DataProviderSet(
        files=LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3"),
        memory=LocalMemoryProvider(tmp_path / "memory.sqlite3"),
        knowledge=LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3"),
    )
