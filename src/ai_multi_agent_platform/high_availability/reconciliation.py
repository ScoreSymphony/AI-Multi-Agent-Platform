"""Concrete failover reconciliation adapters for durable distributed runtime state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ai_multi_agent_platform.distributed.runtime import DispatchState, DistributedRuntime

from .contracts import CoordinationProvider, FencingToken, ReconciliationResult


class DistributedRuntimeFailoverReconciler:
    """Reuse #14 distributed reconciliation as the #89 promotion barrier.

    The adapter does not redispatch lost work. Promotion first reconciles durable ownership and
    stale reservations; only after the Control Plane becomes ACTIVE may ordinary #14 failover
    fencing/redispatch proceed. The fencing token is validated before and after reconciliation so a
    candidate that loses coordination while recovering cannot become authoritative afterwards.
    """

    def __init__(
        self,
        runtime: DistributedRuntime,
        coordinator: CoordinationProvider,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._runtime = runtime
        self._coordinator = coordinator
        self._clock = clock

    async def reconcile(
        self,
        *,
        token: FencingToken,
        previous_epoch: int,
        reason: str,
    ) -> ReconciliationResult:
        await self._coordinator.assert_fence(token)

        before_states = {
            record.job.worker_job_id: record.state for record in self._runtime.records()
        }
        before_reservations = {
            reservation.reservation_id
            for reservation in self._runtime.registry.active_reservations()
        }

        reconciled = await self._runtime.reconcile(
            now=None if self._clock is None else self._clock()
        )

        # Reconciliation may await remote Worker state. Re-prove the same generation before the
        # promotion barrier is allowed to complete.
        await self._coordinator.assert_fence(token)

        after_reservations = {
            reservation.reservation_id
            for reservation in self._runtime.registry.active_reservations()
        }
        expired_reservations = before_reservations - after_reservations

        state_changes = tuple(
            record
            for record in reconciled
            if before_states.get(record.job.worker_job_id) != record.state
        )
        newly_lost = tuple(
            record
            for record in state_changes
            if record.state is DispatchState.LOST
        )

        return ReconciliationResult(
            recovered_items=len(state_changes),
            rejected_stale_items=len(newly_lost) + len(expired_reservations),
            details=(
                f"epoch={token.epoch}",
                f"previous_epoch={previous_epoch}",
                f"reason={reason}",
                f"dispatch_records={len(reconciled)}",
                f"state_changes={len(state_changes)}",
                f"lost_ownership={len(newly_lost)}",
                f"expired_reservations={len(expired_reservations)}",
            ),
        )
