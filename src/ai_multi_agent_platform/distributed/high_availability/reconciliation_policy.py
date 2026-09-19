"""Pure failover-reconciliation classification helpers."""

from __future__ import annotations

from collections.abc import Collection, Mapping

from ai_multi_agent_platform.distributed.runtime import DispatchState

from .contracts import ReconciliationResult


def derive_reconciliation_result(
    *,
    before_states: Mapping[str, DispatchState],
    after_states: Mapping[str, DispatchState],
    before_reservation_ids: Collection[str],
    after_reservation_ids: Collection[str],
    token_epoch: int,
    previous_epoch: int,
    reason: str,
) -> ReconciliationResult:
    """Classify deterministic state/reservation deltas after runtime reconciliation."""

    changed_job_ids = tuple(
        worker_job_id
        for worker_job_id, state in after_states.items()
        if before_states.get(worker_job_id) != state
    )
    newly_lost = sum(
        1 for worker_job_id in changed_job_ids if after_states[worker_job_id] is DispatchState.LOST
    )
    expired_reservations = set(before_reservation_ids) - set(after_reservation_ids)

    return ReconciliationResult(
        recovered_items=len(changed_job_ids),
        rejected_stale_items=newly_lost + len(expired_reservations),
        details=(
            f"epoch={token_epoch}",
            f"previous_epoch={previous_epoch}",
            f"reason={reason}",
            f"dispatch_records={len(after_states)}",
            f"state_changes={len(changed_job_ids)}",
            f"lost_ownership={newly_lost}",
            f"expired_reservations={len(expired_reservations)}",
        ),
    )
