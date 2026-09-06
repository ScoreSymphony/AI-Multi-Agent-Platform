from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.distributed import LocalWorker, WorkerJobRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.high_availability import (
    AvailabilityMode,
    ControlPlaneFailoverService,
    ControlPlaneRole,
    FencingToken,
    InMemoryCoordinationProvider,
    NotLeaderError,
    ReconciliationResult,
)
from ai_multi_agent_platform.high_availability.worker_transport import (
    FencedTransportWorkerDispatcher,
    FencedWorkerTransportEndpoint,
)
from ai_multi_agent_platform.messaging import InProcessMessageTransport
from ai_multi_agent_platform.testing import FakeLifecycleBackend

NOW = datetime(2026, 9, 6, 2, 30, tzinfo=UTC)
LEASE_TTL = timedelta(seconds=5)


class MutableClock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def _job() -> WorkerJobRequest:
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=new_id("task"),
            context=OperationContext(correlation_id="corr:issue-89-promotion-cancel"),
        )
    )


async def _stop_endpoint(
    task: asyncio.Task[None],
    transport: InProcessMessageTransport,
) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await transport.close(graceful=False)


@dataclass
class CancelDuringPromotionReconciler:
    worker_id: str
    worker_job_id: str
    transport: InProcessMessageTransport
    service: ControlPlaneFailoverService | None = None

    async def reconcile(
        self,
        *,
        token: FencingToken,
        previous_epoch: int,
        reason: str,
    ) -> ReconciliationResult:
        del previous_epoch, reason
        assert self.service is not None
        assert self.service.role is ControlPlaneRole.PROMOTING
        assert self.service.fencing_token == token

        client = FencedTransportWorkerDispatcher(
            self.worker_id,
            self.transport,
            authority_check=self.service.require_authority,
            cancel_authority_check=self.service.require_reconciliation_authority,
            control_plane_instance_id=self.service.instance_id,
            response_timeout_seconds=1,
        )

        with pytest.raises(NotLeaderError):
            await client.dispatch(_job())

        cancelled = await client.cancel(self.worker_job_id)
        return ReconciliationResult(
            recovered_items=1,
            details=(f"cancelled_run={cancelled.run_id}",),
        )


def test_promoting_instance_can_reconcile_cancel_without_general_dispatch_authority() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        coordinator = InMemoryCoordinationProvider(clock=clock)
        first = ControlPlaneFailoverService(
            instance_id="control-a",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=LEASE_TTL,
        )
        assert await first.start() is True

        transport = InProcessMessageTransport(provider_id="issue-89-promotion-cancel")
        lifecycle = FakeLifecycleBackend()
        worker_id = new_id("worker")
        worker = LocalWorker(worker_id, lifecycle)
        endpoint = FencedWorkerTransportEndpoint(
            worker,
            transport,
            coordinator=coordinator,
        )
        endpoint_task = asyncio.create_task(endpoint.serve())
        await asyncio.sleep(0)

        first_client = FencedTransportWorkerDispatcher(
            worker_id,
            transport,
            authority_check=first.require_authority,
            control_plane_instance_id=first.instance_id,
            response_timeout_seconds=1,
        )
        job = _job()

        try:
            await first_client.dispatch(job)
            assert len(lifecycle.start_calls) == 1

            clock.advance(LEASE_TTL + timedelta(milliseconds=1))
            reconciler = CancelDuringPromotionReconciler(
                worker_id=worker_id,
                worker_job_id=job.worker_job_id,
                transport=transport,
            )
            second = ControlPlaneFailoverService(
                instance_id="control-b",
                mode=AvailabilityMode.ACTIVE_PASSIVE,
                coordinator=coordinator,
                lease_ttl=LEASE_TTL,
                reconciler=reconciler,
            )
            reconciler.service = second

            assert await second.try_promote(reason="finish-pending-cancel") is True
            assert second.role is ControlPlaneRole.ACTIVE
            assert len(lifecycle.start_calls) == 1
            assert len(lifecycle.cancel_calls) == 1
            status = await second.status()
            assert status.last_reconciliation is not None
            assert status.last_reconciliation.recovered_items == 1
        finally:
            await _stop_endpoint(endpoint_task, transport)

    asyncio.run(scenario())
