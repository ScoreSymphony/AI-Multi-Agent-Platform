from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    InMemoryCoordinatorRepository,
    ReconciliationDisposition,
    RetryState,
    StepCoordinationRecord,
    StepRetryPolicy,
    StepWait,
    WaitResolution,
    WaitType,
)
from ai_multi_agent_platform.coordination.repair import (
    CoordinatorRepairAction,
    CoordinatorRepairService,
)
from ai_multi_agent_platform.coordination.sqlite_repository import _record_from_dict
from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, StepStatus, new_id


def _legacy_record(*, retry_state: str | None = None) -> dict[str, Any]:
    now = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)
    payload: dict[str, Any] = {
        "task_id": new_id("task"),
        "plan_id": new_id("plan"),
        "plan_revision": 1,
        "step_id": new_id("step"),
        "phase": CoordinationPhase.RETRY_SCHEDULED.value,
        "dependency_ids": [],
        "satisfied_dependency_ids": [],
        "latest_run_id": new_id("run"),
        "current_attempt": 1,
        "retry_policy": {
            "max_attempts": 2,
            "initial_delay_seconds": 10.0,
            "multiplier": 2.0,
            "max_delay_seconds": 60.0,
            "retryable_categories": ["transient"],
            "version": 1,
        },
        "retry_due_at": (now + timedelta(seconds=10)).isoformat(),
        "wait": None,
        "predecessor_failure_policy": "fail_fast",
        "processed_keys": ["run:legacy:failed"],
        "reconciliation": "consistent",
        "reconciliation_detail": None,
        "correlation_id": None,
        "causation_id": None,
        "provenance_source": "platform-coordinator",
        "revision": 3,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }
    if retry_state is not None:
        payload["retry_state"] = retry_state
    return payload


def test_legacy_scheduled_retry_without_retry_state_loads_as_scheduled() -> None:
    record = _record_from_dict(_legacy_record())

    assert record.phase is CoordinationPhase.RETRY_SCHEDULED
    assert record.retry_state is RetryState.SCHEDULED
    assert record.retry_due_at is not None


def test_explicit_invalid_retry_state_is_not_silently_migrated() -> None:
    with pytest.raises(ValueError, match="retry_due_at is valid only for a scheduled retry"):
        _record_from_dict(_legacy_record(retry_state=RetryState.NONE.value))


class _UnusedKernel:
    async def get_run(self, task_id: str, run_id: str) -> object:
        raise AssertionError(f"unexpected get_run for {task_id}/{run_id}")


class _RepairCoordinator:
    def __init__(self, repository: InMemoryCoordinatorRepository) -> None:
        self.repository = repository
        self.coordinator_id = "repair-regression"
        self.claim_ttl = timedelta(seconds=30)
        self.kernel = _UnusedKernel()

    async def advance(self, plan_id: str, *, now: datetime | None = None) -> object:
        del plan_id, now
        return object()

    def projection(self, plan_id: str) -> object:
        del plan_id
        return object()


def test_operator_repair_retains_resolved_wait_history_and_terminalizes_retry() -> None:
    owner = OwnerRef(type="user", id="workflow-regression-user")
    plan = Plan(
        task_id=new_id("task"),
        owner_ref=owner,
        active=True,
        project_id=new_id("project"),
    )
    step = Step(
        plan_id=plan.id,
        title="repair resolved wait",
        owner_ref=owner,
        project_id=plan.project_id,
        status=StepStatus.RUNNING,
    )
    created_at = datetime(2026, 9, 7, 18, 0, tzinfo=UTC)
    resolved_wait = StepWait(
        wait_key="resolved-event-wait",
        wait_type=WaitType.EVENT,
        task_id=plan.task_id,
        plan_id=plan.id,
        step_id=step.id,
        owner_ref=owner,
        project_id=plan.project_id,
        event_type="connector.completed",
        correlation_key="corr-resolved",
        created_at=created_at,
        resolved_at=created_at + timedelta(seconds=5),
        resolution=WaitResolution.SATISFIED,
        resolution_key="event:event-resolved",
    )
    record = StepCoordinationRecord(
        task_id=plan.task_id,
        plan_id=plan.id,
        plan_revision=plan.revision,
        step_id=step.id,
        phase=CoordinationPhase.INCONSISTENT,
        current_attempt=2,
        retry_policy=StepRetryPolicy(max_attempts=3),
        retry_state=RetryState.ACTIVE,
        wait=resolved_wait,
        reconciliation=ReconciliationDisposition.MISSING_CANONICAL_RUN,
        revision=1,
        created_at=created_at,
        updated_at=created_at,
    )
    repository = InMemoryCoordinatorRepository()
    repository.create_plan(plan, (step,), (record,))
    coordinator = _RepairCoordinator(repository)
    service = CoordinatorRepairService(cast(Any, coordinator))

    asyncio.run(
        service.repair_step(
            plan_id=plan.id,
            step_id=step.id,
            action=CoordinatorRepairAction.CANCEL_MISSING_RUN,
            expected_revision=record.revision,
            idempotency_key="repair-resolved-wait",
            now=created_at + timedelta(seconds=10),
        )
    )

    repaired = repository.get_step_record(step.id)
    repaired_step = repository.get_plan(plan.id).step(step.id)
    assert repaired.phase is CoordinationPhase.TERMINAL
    assert repaired.retry_state is RetryState.CANCELLED
    assert repaired.wait == resolved_wait
    assert repaired.wait is not None and repaired.wait.resolution is WaitResolution.SATISFIED
    assert repaired_step.status is StepStatus.CANCELLED
