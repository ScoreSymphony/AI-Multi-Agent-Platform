from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.mcp_tasks import SqliteMCPTaskBindingStore
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode


@pytest.mark.asyncio
async def test_sqlite_task_binding_store_keeps_driver_work_off_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqliteMCPTaskBindingStore(tmp_path / "mcp-task-bindings.db")
    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []
    original_connect = store._connect

    def tracked_connect():  # type: ignore[no-untyped-def]
        worker_threads.append(threading.get_ident())
        return original_connect()

    monkeypatch.setattr(store, "_connect", tracked_connect)
    try:
        assert await store.get("mcp:test", "invoke-1") is None
    finally:
        store.close()

    assert worker_threads
    assert event_loop_thread not in worker_threads


class _DelayedFailingStore(SqliteMCPTaskBindingStore):
    def _get_sync(self, provider_id: str, invocation_id: str):  # type: ignore[no-untyped-def]
        del provider_id, invocation_id
        time.sleep(0.05)
        raise ContractError(ErrorCode.BACKEND_ERROR, "persistence failed")


@pytest.mark.asyncio
async def test_sqlite_task_binding_store_worker_failure_wins_pending_cancellation(
    tmp_path: Path,
) -> None:
    store = _DelayedFailingStore(tmp_path / "mcp-task-bindings.db")
    operation = asyncio.create_task(store.get("mcp:test", "invoke-1"))
    await asyncio.sleep(0.01)
    operation.cancel()

    try:
        with pytest.raises(ContractError) as caught:
            await operation
    finally:
        store.close()

    assert caught.value.code is ErrorCode.BACKEND_ERROR
