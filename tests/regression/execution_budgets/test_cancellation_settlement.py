from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from ai_multi_agent_platform.coordination import DurablePlanStepCoordinator
from ai_multi_agent_platform.coordination.models import CoordinationPhase, StepCoordinationRecord
from ai_multi_agent_platform.deployment.task_budget_bindings import (
    TaskBudgetCoordinationBindings,
    TaskBudgetRepairRuntime,
)
from ai_multi_agent_platform.domain import OwnerRef, Step, new_id


class _SettlementBudgets:
    def __init__(
        self,
        *,
        fail_first_release: bool = False,
        block_first_release: bool = False,
        reconcile_failure: BaseException | None = None,
    ) -> None:
        self.retry = SimpleNamespace(name="retry")
        self.parallel = SimpleNamespace(name="parallel")
        self.repair = SimpleNamespace(name="repair")
        self.released: list[str] = []
        self.fail_first_release = fail_first_release
        self.block_first_release = block_first_release
        self.reconcile_failure = reconcile_failure
        self.first_release_started = asyncio.Event()
        self.allow_first_release = asyncio.Event()

    async def admit(self, **kwargs: object) -> object:
        action = kwargs.get("action")
        value = getattr(action, "value", "")
        if value == "repair":
            return self.repair
        return self.retry

    async def require_permitted(self, decision: object) -> None:
        del decision

    async def reconcile(self, decision: object) -> object:
        if self.reconcile_failure is not None:
            raise self.reconcile_failure
        return decision

    async def release(self, decision: Any) -> None:
        name = cast(str, decision.name)
        self.released.append(name)
        if len(self.released) == 1:
            self.first_release_started.set()
            if self.block_first_release:
                await self.allow_first_release.wait()
            if self.fail_first_release:
                raise RuntimeError("synthetic cleanup failure")


class _UnusedCoordinator:
    runtime_repository: object

    def __init__(self) -> None:
        self.runtime_repository = object()


class _RepairInner:
    def __init__(self, failure: BaseException) -> None:
        self._runtime_verification = self
        self._failure = failure
        self.request = SimpleNamespace(
            repair_attempt=0,
            task_id=new_id("task"),
            run_id=new_id("run"),
            correlation_id="repair-correlation",
        )

    async def get_request(self, verification_id: str) -> object:
        del verification_id
        return self.request

    async def _existing_execution(self, *args: object) -> None:
        del args
        return None

    async def start_repair(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        raise self._failure


def _record(*, task_id: str, plan_id: str, step_id: str) -> StepCoordinationRecord:
    return StepCoordinationRecord(
        task_id=task_id,
        plan_id=plan_id,
        plan_revision=1,
        step_id=step_id,
        phase=CoordinationPhase.READY,
        current_attempt=1,
        correlation_id=task_id,
    )


def _binding(budgets: _SettlementBudgets) -> TaskBudgetCoordinationBindings:
    return TaskBudgetCoordinationBindings(
        cast(DurablePlanStepCoordinator, _UnusedCoordinator()),
        budgets,
    )


def _start_case() -> tuple[Step, StepCoordinationRecord]:
    task_id = new_id("task")
    plan_id = new_id("plan")
    step_id = new_id("step")
    return (
        Step(
            plan_id=plan_id,
            id=step_id,
            title="settlement case",
            owner_ref=OwnerRef(type="user", id="settlement-test"),
        ),
        _record(task_id=task_id, plan_id=plan_id, step_id=step_id),
    )


async def _install_parallel_claim(
    bindings: TaskBudgetCoordinationBindings,
    decision: object,
) -> None:
    async def claim(record: StepCoordinationRecord) -> object:
        del record
        return decision

    bindings._claim_parallel = claim  # type: ignore[method-assign]  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize("signal_type", [KeyboardInterrupt, SystemExit])
async def test_start_failure_preserves_process_signal_and_attempts_all_settlement(
    signal_type: type[BaseException],
) -> None:
    budgets = _SettlementBudgets(fail_first_release=True)
    bindings = _binding(budgets)
    await _install_parallel_claim(bindings, budgets.parallel)
    step, record = _start_case()

    async def start(
        _step: Step,
        _record: StepCoordinationRecord,
        _now: datetime,
    ) -> bool:
        raise signal_type("synthetic process signal")

    with pytest.raises(signal_type) as exc_info:
        await bindings._start_with_budget(  # noqa: SLF001
            start,
            step,
            record,
            datetime.now(UTC),
        )

    assert budgets.released == ["retry", "parallel"]
    assert any("RuntimeError" in note for note in getattr(exc_info.value, "__notes__", ()))


@pytest.mark.asyncio
async def test_start_ordinary_exception_preserves_primary_after_cleanup_failure() -> None:
    budgets = _SettlementBudgets(fail_first_release=True)
    bindings = _binding(budgets)
    await _install_parallel_claim(bindings, budgets.parallel)
    step, record = _start_case()
    failure = ValueError("synthetic primary failure")

    async def start(
        _step: Step,
        _record: StepCoordinationRecord,
        _now: datetime,
    ) -> bool:
        raise failure

    with pytest.raises(ValueError) as exc_info:
        await bindings._start_with_budget(  # noqa: SLF001
            start,
            step,
            record,
            datetime.now(UTC),
        )

    assert exc_info.value is failure
    assert budgets.released == ["retry", "parallel"]
    assert any("RuntimeError" in note for note in getattr(exc_info.value, "__notes__", ()))


@pytest.mark.asyncio
async def test_start_cancellation_settles_all_claims_before_propagating_repeated_cancel() -> None:
    budgets = _SettlementBudgets(block_first_release=True)
    bindings = _binding(budgets)
    await _install_parallel_claim(bindings, budgets.parallel)
    step, record = _start_case()

    async def start(
        _step: Step,
        _record: StepCoordinationRecord,
        _now: datetime,
    ) -> bool:
        raise asyncio.CancelledError

    operation = asyncio.create_task(
        bindings._start_with_budget(  # noqa: SLF001
            start,
            step,
            record,
            datetime.now(UTC),
        )
    )
    await asyncio.wait_for(budgets.first_release_started.wait(), timeout=1.0)
    operation.cancel()
    budgets.allow_first_release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(operation, timeout=1.0)

    assert budgets.released == ["retry", "parallel"]


@pytest.mark.asyncio
async def test_false_start_releases_each_claim_exactly_once() -> None:
    budgets = _SettlementBudgets()
    bindings = _binding(budgets)
    await _install_parallel_claim(bindings, budgets.parallel)
    step, record = _start_case()

    async def start(
        _step: Step,
        _record: StepCoordinationRecord,
        _now: datetime,
    ) -> bool:
        return False

    assert not await bindings._start_with_budget(  # noqa: SLF001
        start,
        step,
        record,
        datetime.now(UTC),
    )
    assert budgets.released == ["retry", "parallel"]


@pytest.mark.asyncio
async def test_false_start_cleanup_failure_still_attempts_each_claim_once() -> None:
    budgets = _SettlementBudgets(fail_first_release=True)
    bindings = _binding(budgets)
    await _install_parallel_claim(bindings, budgets.parallel)
    step, record = _start_case()

    async def start(
        _step: Step,
        _record: StepCoordinationRecord,
        _now: datetime,
    ) -> bool:
        return False

    with pytest.raises(RuntimeError, match="synthetic cleanup failure"):
        await bindings._start_with_budget(  # noqa: SLF001
            start,
            step,
            record,
            datetime.now(UTC),
        )

    assert budgets.released == ["retry", "parallel"]


@pytest.mark.asyncio
async def test_started_run_keeps_parallel_claim_when_retry_reconciliation_fails() -> None:
    failure = RuntimeError("synthetic reconcile failure")
    budgets = _SettlementBudgets(reconcile_failure=failure)
    bindings = _binding(budgets)
    await _install_parallel_claim(bindings, budgets.parallel)
    step, record = _start_case()

    async def start(
        _step: Step,
        _record: StepCoordinationRecord,
        _now: datetime,
    ) -> bool:
        return True

    with pytest.raises(RuntimeError) as exc_info:
        await bindings._start_with_budget(  # noqa: SLF001
            start,
            step,
            record,
            datetime.now(UTC),
        )

    assert exc_info.value is failure
    assert budgets.released == ["retry"]


@pytest.mark.asyncio
async def test_repair_cancellation_settles_reservation_before_propagating() -> None:
    budgets = _SettlementBudgets(block_first_release=True)
    inner = _RepairInner(asyncio.CancelledError())
    runtime = TaskBudgetRepairRuntime(cast(Any, inner), budgets)

    operation = asyncio.create_task(
        runtime.start_repair(
            new_id("verification"),
            idempotency_key="repair-cancellation",
        )
    )
    await asyncio.wait_for(budgets.first_release_started.wait(), timeout=1.0)
    operation.cancel()
    budgets.allow_first_release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(operation, timeout=1.0)

    assert budgets.released == ["repair"]
