from __future__ import annotations

from ai_multi_agent_platform.distributed.runtime import DispatchState
from ai_multi_agent_platform.high_availability.reconciliation_policy import (
    derive_reconciliation_result,
)


def test_unchanged_runtime_state_produces_no_recovery_or_stale_items() -> None:
    result = derive_reconciliation_result(
        before_states={"worker_job:a": DispatchState.RUNNING},
        after_states={"worker_job:a": DispatchState.RUNNING},
        before_reservation_ids={"reservation:a"},
        after_reservation_ids={"reservation:a"},
        token_epoch=4,
        previous_epoch=3,
        reason="steady-state",
    )

    assert result.recovered_items == 0
    assert result.rejected_stale_items == 0
    assert result.details == (
        "epoch=4",
        "previous_epoch=3",
        "reason=steady-state",
        "dispatch_records=1",
        "state_changes=0",
        "lost_ownership=0",
        "expired_reservations=0",
    )


def test_lost_ownership_and_expired_reservations_are_counted_as_stale() -> None:
    result = derive_reconciliation_result(
        before_states={
            "worker_job:a": DispatchState.RUNNING,
            "worker_job:b": DispatchState.DISPATCHED,
        },
        after_states={
            "worker_job:a": DispatchState.LOST,
            "worker_job:b": DispatchState.RUNNING,
        },
        before_reservation_ids={"reservation:a", "reservation:b"},
        after_reservation_ids={"reservation:b"},
        token_epoch=8,
        previous_epoch=7,
        reason="leader-promotion",
    )

    assert result.recovered_items == 2
    assert result.rejected_stale_items == 2
    assert "lost_ownership=1" in result.details
    assert "expired_reservations=1" in result.details


def test_non_lost_state_changes_are_recovered_but_not_rejected_as_stale() -> None:
    result = derive_reconciliation_result(
        before_states={"worker_job:a": DispatchState.DISPATCHED},
        after_states={"worker_job:a": DispatchState.RUNNING},
        before_reservation_ids=(),
        after_reservation_ids=(),
        token_epoch=2,
        previous_epoch=1,
        reason="worker-observed",
    )

    assert result.recovered_items == 1
    assert result.rejected_stale_items == 0
    assert "state_changes=1" in result.details
    assert "lost_ownership=0" in result.details


def test_newly_observed_record_is_a_deterministic_state_change() -> None:
    result = derive_reconciliation_result(
        before_states={},
        after_states={"worker_job:new": DispatchState.RUNNING},
        before_reservation_ids=(),
        after_reservation_ids=(),
        token_epoch=1,
        previous_epoch=0,
        reason="initial-promotion",
    )

    assert result.recovered_items == 1
    assert result.rejected_stale_items == 0
    assert "dispatch_records=1" in result.details
