from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.high_availability import (
    AvailabilityMode,
    ControlPlaneFailoverService,
    ControlPlaneRole,
    CoordinationUnavailable,
    FencingToken,
    InMemoryCoordinationProvider,
    PromotionReconciliationError,
    ReconciliationResult,
    StaleFencingToken,
)

NOW = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)
TTL = timedelta(seconds=10)


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


@dataclass
class RecordingReconciler:
    calls: list[tuple[FencingToken, int, str]]
    fail: bool = False

    async def reconcile(
        self,
        *,
        token: FencingToken,
        previous_epoch: int,
        reason: str,
    ) -> ReconciliationResult:
        self.calls.append((token, previous_epoch, reason))
        if self.fail:
            raise RuntimeError("deterministic reconciliation failure")
        return ReconciliationResult(recovered_items=2, rejected_stale_items=1)


def test_single_node_remains_active_without_ha_coordination() -> None:
    async def scenario() -> None:
        service = ControlPlaneFailoverService(
            instance_id="single-node-process",
            mode=AvailabilityMode.SINGLE_NODE,
        )

        assert await service.start() is True
        grant = await service.require_authority()
        status = await service.status()

        assert grant.fencing_token is None
        assert grant.instance_id == "single-node-process"
        assert status.role is ControlPlaneRole.ACTIVE
        assert status.epoch == 0
        assert status.coordination_available is True
        assert status.promotion_count == 0

    asyncio.run(scenario())


def test_active_passive_promotion_fences_stale_old_leader() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        coordinator = InMemoryCoordinationProvider(clock=clock)
        active = ControlPlaneFailoverService(
            instance_id="control-a",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=TTL,
        )
        standby = ControlPlaneFailoverService(
            instance_id="control-b",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=TTL,
        )

        assert await active.start() is True
        assert await standby.start() is False
        assert active.fencing_token is not None
        assert active.fencing_token.epoch == 1

        clock.advance(TTL + timedelta(milliseconds=1))
        assert await standby.try_promote(reason="active-timeout") is True
        assert standby.fencing_token is not None
        assert standby.fencing_token.epoch == 2

        with pytest.raises(StaleFencingToken):
            await active.require_authority()
        assert active.role is ControlPlaneRole.FENCED

        grant = await standby.require_authority()
        assert grant.fencing_token == FencingToken(instance_id="control-b", epoch=2)

    asyncio.run(scenario())


def test_simultaneous_acquisition_yields_exactly_one_active_instance() -> None:
    async def scenario() -> None:
        coordinator = InMemoryCoordinationProvider(clock=MutableClock())
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

        results = await asyncio.gather(first.start(), second.start())
        state = await coordinator.inspect()

        assert sum(results) == 1
        assert state.epoch == 1
        assert state.owner_instance_id in {"control-a", "control-b"}
        roles = {first.role, second.role}
        assert roles == {ControlPlaneRole.ACTIVE, ControlPlaneRole.STANDBY}

    asyncio.run(scenario())


def test_step_down_allows_new_leader_with_monotonic_epoch() -> None:
    async def scenario() -> None:
        coordinator = InMemoryCoordinationProvider(clock=MutableClock())
        first = ControlPlaneFailoverService(
            instance_id="control-a",
            mode=AvailabilityMode.WARM_STANDBY,
            coordinator=coordinator,
            lease_ttl=TTL,
        )
        second = ControlPlaneFailoverService(
            instance_id="control-b",
            mode=AvailabilityMode.WARM_STANDBY,
            coordinator=coordinator,
            lease_ttl=TTL,
        )

        assert await first.start() is True
        assert first.fencing_token == FencingToken(instance_id="control-a", epoch=1)
        await first.step_down()
        assert first.role is ControlPlaneRole.STANDBY

        assert await second.try_promote(reason="operator-promotion") is True
        assert second.fencing_token == FencingToken(instance_id="control-b", epoch=2)

    asyncio.run(scenario())


def test_coordination_outage_fails_authority_closed() -> None:
    async def scenario() -> None:
        coordinator = InMemoryCoordinationProvider(clock=MutableClock())
        service = ControlPlaneFailoverService(
            instance_id="control-a",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=TTL,
        )
        assert await service.start() is True

        coordinator.set_available(False)
        with pytest.raises(CoordinationUnavailable):
            await service.require_authority()
        assert service.role is ControlPlaneRole.FENCED

        status = await service.status()
        assert status.coordination_available is False
        assert status.role is ControlPlaneRole.FENCED

    asyncio.run(scenario())


def test_promotion_reconciliation_is_a_barrier_and_failure_releases_lease() -> None:
    async def scenario() -> None:
        coordinator = InMemoryCoordinationProvider(clock=MutableClock())
        failed_reconciler = RecordingReconciler(calls=[], fail=True)
        failed = ControlPlaneFailoverService(
            instance_id="control-a",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=TTL,
            reconciler=failed_reconciler,
        )

        with pytest.raises(PromotionReconciliationError):
            await failed.start(reason="startup-recovery")
        assert failed.role is ControlPlaneRole.FENCED
        assert failed_reconciler.calls[0][1:] == (0, "startup-recovery")

        healthy_reconciler = RecordingReconciler(calls=[])
        healthy = ControlPlaneFailoverService(
            instance_id="control-b",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=TTL,
            reconciler=healthy_reconciler,
        )
        assert await healthy.start(reason="recovery-after-failed-promotion") is True
        assert healthy.fencing_token == FencingToken(instance_id="control-b", epoch=2)
        assert healthy_reconciler.calls[0][1:] == (1, "recovery-after-failed-promotion")

        status = await healthy.status()
        assert status.role is ControlPlaneRole.ACTIVE
        assert status.promotion_count == 1
        assert status.last_reconciliation == ReconciliationResult(
            recovered_items=2,
            rejected_stale_items=1,
        )

    asyncio.run(scenario())
