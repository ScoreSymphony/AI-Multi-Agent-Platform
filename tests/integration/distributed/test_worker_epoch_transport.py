from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.distributed.models import WorkerJobRequest
from ai_multi_agent_platform.distributed.transport import (
    RemoteWorkerTransportError,
    TransportWorkerDispatcher,
)
from ai_multi_agent_platform.distributed.worker import LocalWorker
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.high_availability import (
    AuthorityGrant,
    AvailabilityMode,
    ControlPlaneFailoverService,
    InMemoryCoordinationProvider,
)
from ai_multi_agent_platform.high_availability.worker_transport import (
    FencedTransportWorkerDispatcher,
    FencedWorkerTransportEndpoint,
)
from ai_multi_agent_platform.messaging import InProcessMessageTransport
from ai_multi_agent_platform.testing import FakeLifecycleBackend

NOW = datetime(2026, 9, 6, 1, 0, tzinfo=UTC)
TTL = timedelta(seconds=5)


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
            context=OperationContext(correlation_id="corr:issue-89-worker-fence"),
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


def test_fenced_worker_rejects_delayed_dispatch_from_stale_control_plane() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        coordinator = InMemoryCoordinationProvider(clock=clock)
        first = ControlPlaneFailoverService(
            instance_id="control-a",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=TTL,
        )
        second = ControlPlaneFailoverService(
            instance_id="control-b",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=TTL,
        )
        assert await first.start() is True
        assert await second.start() is False
        stale_grant = await first.require_authority()

        transport = InProcessMessageTransport(provider_id="issue-89-worker-fence")
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
        first_job = _job()
        try:
            handle = await first_client.dispatch(first_job)
            assert handle.run_id == first_job.execution.run_id
            assert len(lifecycle.start_calls) == 1

            clock.advance(TTL + timedelta(milliseconds=1))
            assert await second.try_promote(reason="worker-failover") is True

            async def stale_authority() -> AuthorityGrant:
                return stale_grant

            stale_client = FencedTransportWorkerDispatcher(
                worker_id,
                transport,
                authority_check=stale_authority,
                control_plane_instance_id=first.instance_id,
                response_timeout_seconds=1,
            )
            replacement_job = _job()
            with pytest.raises(RemoteWorkerTransportError) as rejected:
                await stale_client.dispatch(replacement_job)
            assert rejected.value.category == "stale_control_plane_fence"
            assert len(lifecycle.start_calls) == 1

            second_client = FencedTransportWorkerDispatcher(
                worker_id,
                transport,
                authority_check=second.require_authority,
                control_plane_instance_id=second.instance_id,
                response_timeout_seconds=1,
            )
            promoted_handle = await second_client.dispatch(replacement_job)
            assert promoted_handle.run_id == replacement_job.execution.run_id
            assert len(lifecycle.start_calls) == 2
        finally:
            await _stop_endpoint(endpoint_task, transport)

    asyncio.run(scenario())


def test_fenced_worker_requires_epoch_for_dispatch_but_keeps_base_transport_optional() -> None:
    async def scenario() -> None:
        coordinator = InMemoryCoordinationProvider()
        active = ControlPlaneFailoverService(
            instance_id="control-a",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=TTL,
        )
        assert await active.start() is True

        transport = InProcessMessageTransport(provider_id="issue-89-worker-fence-required")
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

        legacy_client = TransportWorkerDispatcher(
            worker_id,
            transport,
            response_timeout_seconds=1,
        )
        try:
            with pytest.raises(RemoteWorkerTransportError) as rejected:
                await legacy_client.dispatch(_job())
            assert rejected.value.category == "control_plane_fence_required"
            assert lifecycle.start_calls == []
        finally:
            await _stop_endpoint(endpoint_task, transport)

    asyncio.run(scenario())


def test_fenced_worker_rejects_stale_cancel_after_promotion() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        coordinator = InMemoryCoordinationProvider(clock=clock)
        first = ControlPlaneFailoverService(
            instance_id="control-a",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=TTL,
        )
        second = ControlPlaneFailoverService(
            instance_id="control-b",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=TTL,
        )
        assert await first.start() is True
        assert await second.start() is False
        stale_grant = await first.require_authority()

        transport = InProcessMessageTransport(provider_id="issue-89-worker-cancel-fence")
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
            clock.advance(TTL + timedelta(milliseconds=1))
            assert await second.try_promote(reason="cancel-failover") is True

            async def stale_authority() -> AuthorityGrant:
                return stale_grant

            stale_client = FencedTransportWorkerDispatcher(
                worker_id,
                transport,
                authority_check=stale_authority,
                control_plane_instance_id=first.instance_id,
                response_timeout_seconds=1,
            )
            with pytest.raises(RemoteWorkerTransportError) as rejected:
                await stale_client.cancel(job.worker_job_id)
            assert rejected.value.category == "stale_control_plane_fence"
            assert lifecycle.cancel_calls == []

            second_client = FencedTransportWorkerDispatcher(
                worker_id,
                transport,
                authority_check=second.require_authority,
                control_plane_instance_id=second.instance_id,
                response_timeout_seconds=1,
            )
            cancelled = await second_client.cancel(job.worker_job_id)
            assert cancelled.run_id == job.execution.run_id
            assert len(lifecycle.cancel_calls) == 1
        finally:
            await _stop_endpoint(endpoint_task, transport)

    asyncio.run(scenario())
