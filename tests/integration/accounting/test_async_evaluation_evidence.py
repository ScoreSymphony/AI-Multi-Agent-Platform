"""Accounting-backed Evaluation evidence stays off the event loop for issue #892."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

from ai_multi_agent_platform.accounting import (
    AccountingService,
    MeasurementQuality,
    SQLiteUsageStore,
    UsageRecord,
    UsageScope,
)
from ai_multi_agent_platform.evaluation.evidence import (
    AccountingEvaluationEvidenceProvider,
    CompositeEvaluationEvidenceProvider,
)


class _RecordingUsageStore(SQLiteUsageStore):
    def __init__(self, path: Path, *, delay: float = 0.05) -> None:
        self.connection_threads: list[int] = []
        self.delay_connections = False
        self._delay = delay
        super().__init__(path)
        self.delay_connections = True

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.get_ident())
        if self.delay_connections:
            time.sleep(self._delay)
        return super()._connect()


def test_accounting_evaluation_evidence_uses_awaitable_runtime_reads(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _RecordingUsageStore(tmp_path / "usage.sqlite3")
        accounting = AccountingService(store)
        accounting.record(
            UsageRecord(
                metric_type="runtime.tokens",
                unit="count",
                quality=MeasurementQuality.MEASURED,
                source="test",
                quantity=3.0,
                scope=UsageScope(task_id="task-evidence", run_id="run-evidence"),
            )
        )
        provider = CompositeEvaluationEvidenceProvider(
            (AccountingEvaluationEvidenceProvider(accounting),)
        )
        event_loop_thread = threading.get_ident()
        before = len(store.connection_threads)

        pending = asyncio.create_task(
            provider.async_collect(task_id="task-evidence", run_id="run-evidence")
        )
        await asyncio.sleep(0.01)

        assert not pending.done()
        evidence = await pending
        assert evidence.metrics["accounting:runtime.tokens:count"] == 3.0
        runtime_threads = store.connection_threads[before:]
        assert runtime_threads
        assert all(thread_id != event_loop_thread for thread_id in runtime_threads)

    asyncio.run(scenario())
