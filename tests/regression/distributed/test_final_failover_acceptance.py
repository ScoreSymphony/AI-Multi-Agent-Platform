from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.high_availability import (
    AvailabilityMode,
    ControlPlaneFailoverService,
    InMemoryCoordinationProvider,
    StaleFencingToken,
)
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.testing.sqlite_events import SqliteEventProvider

NOW = datetime(2026, 9, 6, 4, 0, tzinfo=UTC)
TTL = timedelta(seconds=5)
PASSWORD = "correct horse battery staple"


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


def _service(
    instance_id: str,
    coordinator: InMemoryCoordinationProvider,
) -> ControlPlaneFailoverService:
    return ControlPlaneFailoverService(
        instance_id=instance_id,
        mode=AvailabilityMode.ACTIVE_PASSIVE,
        coordinator=coordinator,
        lease_ttl=TTL,
    )


def _kernel(path: Path, lifecycle: FakeLifecycleBackend) -> PlatformKernel:
    return PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=lifecycle,
        repository=SqliteKernelRepository(path),
    )


def test_duplicate_command_replay_after_promotion_does_not_duplicate_task_or_run(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = MutableClock()
        coordinator = InMemoryCoordinationProvider(clock=clock)
        first = _service("control-a", coordinator)
        second = _service("control-b", coordinator)
        assert await first.start(reason="initial") is True
        assert await second.start(reason="standby") is False

        lifecycle = FakeLifecycleBackend()
        database = tmp_path / "kernel.sqlite3"
        first_kernel = _kernel(database, lifecycle)
        task = await first_kernel.create_task(
            idempotency_key="ha-create",
            title="Failover task",
            objective="Prove duplicate-command replay across promotion",
            owner_type="user",
            owner_id="owner",
        )
        await first_kernel.ready_task(idempotency_key="ha-ready", task_id=task.task_id)
        run = await first_kernel.start_task(idempotency_key="ha-start", task_id=task.task_id)
        assert len(lifecycle.start_calls) == 1

        clock.advance(TTL + timedelta(milliseconds=1))
        assert await second.try_promote(reason="leader-loss") is True
        with pytest.raises(StaleFencingToken):
            await first.require_authority()
        await second.require_authority()

        promoted_kernel = _kernel(database, lifecycle)
        same_task = await promoted_kernel.create_task(
            idempotency_key="ha-create",
            title="Failover task",
            objective="Prove duplicate-command replay across promotion",
            owner_type="user",
            owner_id="owner",
        )
        await promoted_kernel.ready_task(idempotency_key="ha-ready", task_id=task.task_id)
        same_run = await promoted_kernel.start_task(
            idempotency_key="ha-start",
            task_id=task.task_id,
        )

        assert same_task.task_id == task.task_id
        assert same_run.run_id == run.run_id
        assert len(lifecycle.start_calls) == 1
        event_types = [event.event_type for event in await promoted_kernel.history(task.task_id)]
        assert event_types.count("task.created") == 1
        assert event_types.count("run.created") == 1

    asyncio.run(scenario())


def test_client_stream_reconnect_after_promotion_resumes_from_durable_cursor(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = MutableClock()
        coordinator = InMemoryCoordinationProvider(clock=clock)
        first = _service("control-a", coordinator)
        second = _service("control-b", coordinator)
        assert await first.start(reason="initial") is True

        lifecycle = FakeLifecycleBackend()
        kernel_database = tmp_path / "kernel.sqlite3"
        events_database = tmp_path / "events.sqlite3"
        first_events = SqliteEventProvider(events_database)
        first_kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=SqliteKernelRepository(kernel_database),
            event_sink=first_events,
        )
        task = await first_kernel.create_task(
            idempotency_key="stream-create",
            title="Stream failover",
            objective="Prove cursor replay across Control Plane replacement",
            owner_type="user",
            owner_id="owner",
        )
        first_batch = await first_events.read(task.task_id)
        assert first_batch
        cursor = first_batch[-1].id

        clock.advance(TTL + timedelta(milliseconds=1))
        assert await second.try_promote(reason="stream-failover") is True
        promoted_events = SqliteEventProvider(events_database)
        promoted_kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=SqliteKernelRepository(kernel_database),
            event_sink=promoted_events,
        )
        await promoted_kernel.ready_task(
            idempotency_key="stream-ready",
            task_id=task.task_id,
        )

        replayed = [
            event async for event in promoted_events.subscribe(task.task_id, after_event_id=cursor)
        ]
        assert replayed
        assert all(event.id != cursor for event in replayed)
        assert replayed[0].event_type == "task.ready"
        combined = first_batch + tuple(replayed)
        assert len({event.id for event in combined}) == len(combined)

    asyncio.run(scenario())


def test_authentication_session_continuity_survives_control_plane_promotion(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = MutableClock()
        coordinator = InMemoryCoordinationProvider(clock=clock)
        first = _service("control-a", coordinator)
        second = _service("control-b", coordinator)
        assert await first.start(reason="initial") is True

        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        first_deployment = build_single_node_deployment(config)
        admin = first_deployment.bootstrap_admin("admin", PASSWORD)
        login = first_deployment.authentication.login("admin", PASSWORD)
        credential = first_deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="ha-auth-continuity",
        )

        clock.advance(TTL + timedelta(milliseconds=1))
        assert await second.try_promote(reason="auth-failover") is True
        with pytest.raises(StaleFencingToken):
            await first.require_authority()
        await second.require_authority()

        promoted_deployment = build_single_node_deployment(config)
        bearer_actor = promoted_deployment.authentication.authenticate_bearer(credential.secret)
        session_actor = promoted_deployment.authentication.authenticate_session(
            login.session.token,
            csrf_token=login.session.csrf_token,
            require_csrf=True,
        )
        assert bearer_actor.identity.actor_id == admin.user_id
        assert session_actor.identity.actor_id == admin.user_id
        assert promoted_deployment.authorization.has_policy(admin.user_id)

    asyncio.run(scenario())
