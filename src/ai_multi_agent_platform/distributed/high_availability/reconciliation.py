"""Concrete failover reconciliation adapters for durable distributed runtime state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ai_multi_agent_platform.distributed.runtime import DistributedRuntime

from .contracts import CoordinationProvider, FencingToken, ReconciliationResult
from .reconciliation_policy import derive_reconciliation_result


class DistributedRuntimeFailoverReconciler:
    """Reuse distributed reconciliation as the high-availability promotion barrier.

    The adapter does not redispatch lost work. Promotion first reconciles durable ownership and
    stale reservations; only after the Control Plane becomes ACTIVE may distributed failover
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

        after_states = {record.job.worker_job_id: record.state for record in reconciled}
        after_reservations = {
            reservation.reservation_id
            for reservation in self._runtime.registry.active_reservations()
        }
        return derive_reconciliation_result(
            before_states=before_states,
            after_states=after_states,
            before_reservation_ids=before_reservations,
            after_reservation_ids=after_reservations,
            token_epoch=token.epoch,
            previous_epoch=previous_epoch,
            reason=reason,
        )
