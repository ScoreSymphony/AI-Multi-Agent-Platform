from __future__ import annotations

import asyncio
from typing import cast

import pytest

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    OperationContext,
)
from ai_multi_agent_platform.distributed import (
    DispatchRecord,
    DispatchState,
    DistributedLifecycleBackend,
    DistributedRuntime,
    WorkerJobRequest,
)
from ai_multi_agent_platform.domain import RunStatus, new_id


class _LostRuntime:
    def __init__(self, record: DispatchRecord) -> None:
        self.record = record

    async def reconcile(self) -> tuple[DispatchRecord, ...]:
        return (self.record,)

    def get_record(self, _worker_job_id: str) -> DispatchRecord:
        return self.record


def test_lost_dispatch_takes_precedence_over_stale_running_snapshot() -> None:
    run_id = new_id("run")
    task_id = new_id("task")
    request = ExecutionRequest(
        run_id=run_id,
        subject_type="task",
        subject_id=task_id,
        context=OperationContext(correlation_id="issue707-lost-stale-snapshot"),
    )
    job = WorkerJobRequest(execution=request)
    record = DispatchRecord(
        job=job,
        worker_id=new_id("worker"),
        reservation_id=new_id("reservation"),
        state=DispatchState.LOST,
        handle=ExecutionHandle(run_id=run_id, backend_ref="stale-worker-backend"),
        snapshot=ExecutionSnapshot(run_id=run_id, status=RunStatus.RUNNING),
    )
    runtime = cast(DistributedRuntime, _LostRuntime(record))
    backend = DistributedLifecycleBackend(runtime)

    with pytest.raises(ContractError) as error:
        asyncio.run(backend.get(run_id, request.context))

    assert error.value.code is ErrorCode.UNAVAILABLE
    assert error.value.retryable is True
