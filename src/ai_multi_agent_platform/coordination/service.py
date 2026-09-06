"""Platform-owned durable Plan/Step runtime coordinator for issue #384."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import (
    OwnerRef,
    Plan,
    RunStatus,
    Step,
    StepStatus,
    TaskStatus,
)
from ai_multi_agent_platform.kernel.models import RecoveryReport, RunState, TaskState
from ai_multi_agent_platform.observability import (
    FailureComponent,
    Telemetry,
    TelemetryContext,
    TelemetryOutcome,
)

from .models import (
    ApprovalOutcome,
    CoordinationPhase,
    CoordinatorClaim,
    PlanCoordinationProjection,
    PredecessorFailurePolicy,
    ReconciliationDisposition,
    StepCoordinationProjection,
    StepCoordinationRecord,
    StepRetryPolicy,
    StepWait,
    WaitResolution,
    WaitType,
)
from .repository import CoordinatorRepository


class CanonicalRunKernel(Protocol):
    async def get_task(self, task_id: str) -> TaskState: ...

    async def get_run(self, task_id: str, run_id: str) -> RunState: ...

    async def create_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        subject_type: Literal["task", "step"] = "task",
        subject_id: str | None = None,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> RunState: ...

    async def start_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> RunState: ...

    async def cancel_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> RunState: ...

    async def complete_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState: ...

    async def fail_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        reason: str | None = None,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState: ...

    async def cancel_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState: ...

    async def recover_task(self, task_id: str) -> RecoveryReport: ...


_TERMINAL_STEPS = frozenset(
    {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.CANCELLED}
)
_TERMINAL_RUNS = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.TIMED_OUT}
)
_SUCCESSFUL_PREDECESSORS = frozenset({StepStatus.SUCCEEDED, StepStatus.SKIPPED})


class DurablePlanStepCoordinator:
    """Advance canonical task-bound Plans without becoming a second public lifecycle."""

    def __init__(
        self,
        *,
        repository: CoordinatorRepository,
        kernel: CanonicalRunKernel,
        coordinator_id: str,
        telemetry: Telemetry | None = None,
        claim_ttl: timedelta = timedelta(seconds=30),
    ) -> None:
        if not coordinator_id.strip():
            raise ValueError("coordinator_id must not be blank")
        if claim_ttl.total_seconds() <= 0:
            raise ValueError("claim_ttl must be positive")
        self.repository = repository
        self.kernel = kernel
        self.coordinator_id = coordinator_id
        self.telemetry = telemetry or Telemetry()
        self.claim_ttl = claim_ttl

    async def register_plan(
        self,
        plan: Plan,
        steps: tuple[Step, ...],
        *,
        retry_policies: dict[str, StepRetryPolicy] | None = None,
        predecessor_failure_policy: PredecessorFailurePolicy = PredecessorFailurePolicy.FAIL_FAST,
    ) -> PlanCoordinationProjection:
        """Register the exact active canonical Plan already present in kernel truth."""

        self._validate_graph(plan, steps)
        task = await self.kernel.get_task(plan.task_id)
        if task.plan_ref != plan.id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "coordinator Plan does not match the active canonical task Plan",
                details={"task_id": plan.task_id, "plan_id": plan.id},
            )
        if set(task.step_ids) != {step.id for step in steps}:
            raise ContractError(
                ErrorCode.CONFLICT,
                "coordinator Steps do not match the active canonical task Plan",
            )
        policies = retry_policies or {}
        unknown = set(policies) - {step.id for step in steps}
        if unknown:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "retry policy references unknown Steps",
                details={"step_ids": cast(JsonValue, sorted(unknown))},
            )
        records = tuple(
            StepCoordinationRecord(
                task_id=plan.task_id,
                plan_id=plan.id,
                plan_revision=plan.revision,
                step_id=step.id,
                phase=CoordinationPhase.BLOCKED,
                dependency_ids=step.depends_on,
                retry_policy=policies.get(step.id, StepRetryPolicy()),
                predecessor_failure_policy=predecessor_failure_policy,
                correlation_id=plan.task_id,
                provenance_source="platform-coordinator",
            )
            for step in steps
        )
        self.repository.create_plan(plan, steps, records)
        self._emit("coordination.plan.registered", plan.task_id, plan.id, None)
        await self.advance(plan.id)
        return self.projection(plan.id)

    async def advance(
        self,
        plan_id: str,
        *,
        now: datetime | None = None,
    ) -> PlanCoordinationProjection:
        """Deterministically activate dependencies, due retries and ready attempts."""

        current_time = self._now(now)
        made_progress = True
        while made_progress:
            made_progress = False
            state = self.repository.get_plan(plan_id)
            by_id = {step.id: step for step in state.steps}
            for record in self.repository.list_step_records(plan_id):
                step = by_id[record.step_id]
                if record.phase is CoordinationPhase.RETRY_SCHEDULED:
                    if record.retry_due_at is not None and record.retry_due_at <= current_time:
                        made_progress = (
                            await self._activate_retry(step, record, current_time) or made_progress
                        )
                    continue
                if record.phase is CoordinationPhase.BLOCKED:
                    made_progress = (
                        await self._refresh_dependencies(step, record, by_id, current_time)
                        or made_progress
                    )
                    continue
                if record.phase is CoordinationPhase.READY:
                    made_progress = (
                        await self._start_attempt(step, record, current_time) or made_progress
                    )
        await self._aggregate_task(plan_id)
        return self.projection(plan_id)

    async def observe_run(
        self,
        *,
        task_id: str,
        run_id: str,
        failure_category: str | None = None,
        observation_key: str | None = None,
        now: datetime | None = None,
    ) -> PlanCoordinationProjection:
        """Consume a canonical Run outcome exactly once and progress its Step."""

        current_time = self._now(now)
        run = await self.kernel.get_run(task_id, run_id)
        if run.run.subject_type != "step":
            raise ContractError(ErrorCode.INVALID_REQUEST, "coordinator accepts only Step Runs")
        if run.status not in _TERMINAL_RUNS:
            raise ContractError(ErrorCode.CONFLICT, f"run {run_id} is not terminal")
        record = self.repository.get_step_record(run.run.subject_id)
        if record.task_id != task_id:
            raise ContractError(ErrorCode.CONFLICT, "Run/Step task scope mismatch")
        key = observation_key or f"run:{run_id}:{run.status.value}"
        if key in record.processed_keys:
            return self.projection(record.plan_id)

        claim = self._claim(record.step_id, current_time)
        if claim is None:
            return self.projection(record.plan_id)
        try:
            state = self.repository.get_plan(record.plan_id)
            step = state.step(record.step_id)
            current = self.repository.get_step_record(record.step_id)
            if key in current.processed_keys:
                return self.projection(record.plan_id)
            next_step = step
            next_record = replace(
                current,
                processed_keys=(*current.processed_keys, key),
                latest_run_id=run_id,
            )

            if run.status is RunStatus.SUCCEEDED:
                if step.status is StepStatus.RUNNING:
                    next_step = step.transition_to(StepStatus.SUCCEEDED)
                    next_record = replace(next_record, phase=CoordinationPhase.TERMINAL)
                elif step.status is StepStatus.WAITING and current.wait is not None:
                    next_record = replace(next_record, phase=CoordinationPhase.WAITING)
                elif step.status is not StepStatus.SUCCEEDED:
                    next_record = replace(
                        next_record,
                        phase=CoordinationPhase.INCONSISTENT,
                        reconciliation=ReconciliationDisposition.INCONSISTENT,
                        reconciliation_detail="successful Run conflicts with canonical Step status",
                    )
            elif run.status in {RunStatus.FAILED, RunStatus.TIMED_OUT}:
                category = failure_category or run.status.value
                if step.status in {StepStatus.RUNNING, StepStatus.WAITING}:
                    next_step = step.transition_to(StepStatus.FAILED)
                next_attempt = max(current.current_attempt, run.attempt) + 1
                if current.retry_policy.permits(category=category, next_attempt=next_attempt):
                    next_record = replace(
                        next_record,
                        phase=CoordinationPhase.RETRY_SCHEDULED,
                        current_attempt=max(current.current_attempt, run.attempt),
                        retry_due_at=(
                            current_time + current.retry_policy.delay_for_attempt(next_attempt)
                        ),
                        wait=None,
                    )
                    self._emit(
                        "coordination.retry.scheduled",
                        task_id,
                        current.plan_id,
                        current.step_id,
                        run_id=run_id,
                        attributes={"attempt": next_attempt, "category": category},
                    )
                else:
                    next_record = replace(
                        next_record,
                        phase=CoordinationPhase.TERMINAL,
                        current_attempt=max(current.current_attempt, run.attempt),
                        retry_due_at=None,
                        wait=None,
                    )
                    self._emit(
                        "coordination.retry.exhausted",
                        task_id,
                        current.plan_id,
                        current.step_id,
                        run_id=run_id,
                        outcome=TelemetryOutcome.FAILED,
                        attributes={"category": category},
                    )
            else:
                if step.status in {StepStatus.RUNNING, StepStatus.WAITING}:
                    next_step = step.transition_to(StepStatus.CANCELLED)
                next_record = replace(
                    next_record,
                    phase=CoordinationPhase.TERMINAL,
                    retry_due_at=None,
                    wait=None,
                )

            self.repository.save_step(
                step=next_step,
                record=next_record,
                expected_revision=current.revision,
                claim=claim,
                now=current_time,
            )
            self._emit(
                "coordination.run.observed",
                task_id,
                current.plan_id,
                current.step_id,
                run_id=run_id,
                outcome=self._run_outcome(run.status),
            )
        finally:
            self.repository.release_claim(claim)
        await self.advance(record.plan_id, now=current_time)
        return self.projection(record.plan_id)

    async def wait_step(
        self,
        wait: StepWait,
        *,
        now: datetime | None = None,
    ) -> PlanCoordinationProjection:
        """Persist a backend-neutral wait without raw provider payloads or secrets."""

        current_time = self._now(now)
        state = self.repository.get_plan(wait.plan_id)
        step = state.step(wait.step_id)
        record = self.repository.get_step_record(wait.step_id)
        self._validate_wait_scope(wait, state.plan, step, record)
        if record.wait is not None:
            if record.wait.wait_key == wait.wait_key:
                return self.projection(wait.plan_id)
            raise ContractError(ErrorCode.CONFLICT, "Step already has a different durable wait")
        if (
            step.status is not StepStatus.RUNNING
            or record.phase is not CoordinationPhase.ATTEMPT_ACTIVE
        ):
            raise ContractError(ErrorCode.CONFLICT, "only an active running Step can enter a wait")
        claim = self._required_claim(step.id, current_time)
        try:
            waiting = step.transition_to(StepStatus.WAITING)
            updated = replace(record, phase=CoordinationPhase.WAITING, wait=wait)
            self.repository.save_step(
                step=waiting,
                record=updated,
                expected_revision=record.revision,
                claim=claim,
                now=current_time,
            )
        finally:
            self.repository.release_claim(claim)
        self._emit(
            "coordination.wait.created",
            record.task_id,
            record.plan_id,
            record.step_id,
            run_id=record.latest_run_id,
            attributes={"wait_type": wait.wait_type.value},
        )
        return self.projection(wait.plan_id)

    async def resolve_approval(
        self,
        *,
        step_id: str,
        approval_id: str,
        subject_type: str,
        subject_id: str,
        action: str,
        outcome: ApprovalOutcome,
        resolution_key: str,
        owner_ref: OwnerRef,
        project_id: str | None,
        now: datetime | None = None,
    ) -> PlanCoordinationProjection:
        record = self.repository.get_step_record(step_id)
        if resolution_key in record.processed_keys:
            return self.projection(record.plan_id)
        wait = record.wait
        if wait is None or wait.wait_type is not WaitType.APPROVAL:
            raise ContractError(ErrorCode.CONFLICT, "Step is not waiting for an Approval")
        if (
            wait.approval_id != approval_id
            or wait.approval_subject_type != subject_type
            or wait.approval_subject_id != subject_id
            or wait.approval_action != action
        ):
            raise ContractError(
                ErrorCode.CONFLICT, "Approval identity/subject/action does not match wait"
            )
        self._require_scope(wait, owner_ref, project_id)
        resolution = {
            "approved": WaitResolution.SATISFIED,
            "rejected": WaitResolution.REJECTED,
            "expired": WaitResolution.EXPIRED,
            "cancelled": WaitResolution.CANCELLED,
        }[outcome]
        return await self._resolve_wait(step_id, resolution, resolution_key, self._now(now))

    async def resolve_event(
        self,
        *,
        step_id: str,
        event_id: str,
        event_type: str,
        correlation_key: str,
        owner_ref: OwnerRef,
        project_id: str | None,
        now: datetime | None = None,
    ) -> PlanCoordinationProjection:
        """Resolve an Event wait from canonical identity/correlation only."""

        if not event_id.strip():
            raise ValueError("event_id must not be blank")
        resolution_key = f"event:{event_id}"
        record = self.repository.get_step_record(step_id)
        if resolution_key in record.processed_keys:
            return self.projection(record.plan_id)
        wait = record.wait
        if wait is None or wait.wait_type is not WaitType.EVENT:
            raise ContractError(ErrorCode.CONFLICT, "Step is not waiting for a canonical Event")
        self._require_scope(wait, owner_ref, project_id)
        if wait.event_type != event_type or wait.correlation_key != correlation_key:
            raise ContractError(ErrorCode.CONFLICT, "Event type/correlation does not match wait")
        return await self._resolve_wait(
            step_id,
            WaitResolution.SATISFIED,
            resolution_key,
            self._now(now),
        )

    async def resolve_external_job(
        self,
        *,
        step_id: str,
        external_job_ref: str,
        resolution: WaitResolution,
        resolution_key: str,
        owner_ref: OwnerRef,
        project_id: str | None,
        now: datetime | None = None,
    ) -> PlanCoordinationProjection:
        """Resolve an adapter-neutral external-job wait by canonical reference only."""

        record = self.repository.get_step_record(step_id)
        if resolution_key in record.processed_keys:
            return self.projection(record.plan_id)
        wait = record.wait
        if wait is None or wait.wait_type is not WaitType.EXTERNAL_JOB:
            raise ContractError(ErrorCode.CONFLICT, "Step is not waiting for an external job")
        self._require_scope(wait, owner_ref, project_id)
        if wait.external_job_ref != external_job_ref:
            raise ContractError(ErrorCode.CONFLICT, "external job reference does not match wait")
        return await self._resolve_wait(step_id, resolution, resolution_key, self._now(now))

    async def process_due(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[PlanCoordinationProjection, ...]:
        """Resume due deadline waits/retries after normal operation or process restart."""

        current_time = self._now(now)
        changed: set[str] = set()
        for plan in self.repository.list_active_plans():
            for record in self.repository.list_step_records(plan.plan.id):
                wait = record.wait
                if (
                    record.phase is CoordinationPhase.WAITING
                    and wait is not None
                    and wait.wait_type is WaitType.DEADLINE
                    and wait.deadline_at is not None
                    and wait.deadline_at <= current_time
                ):
                    await self._resolve_wait(
                        record.step_id,
                        WaitResolution.SATISFIED,
                        f"deadline:{wait.wait_key}",
                        current_time,
                    )
                    changed.add(plan.plan.id)
                elif (
                    record.phase is CoordinationPhase.RETRY_SCHEDULED
                    and record.retry_due_at is not None
                    and record.retry_due_at <= current_time
                ):
                    await self.advance(plan.plan.id, now=current_time)
                    changed.add(plan.plan.id)
        return tuple(self.projection(plan_id) for plan_id in sorted(changed))

    async def cancel_plan(
        self,
        plan_id: str,
        *,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PlanCoordinationProjection:
        """Suppress future wakeups/retries and propagate cancellation to canonical truth."""

        if not idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")
        current_time = self._now(now)
        state = self.repository.get_plan(plan_id)
        for record in self.repository.list_step_records(plan_id):
            step = self.repository.get_plan(plan_id).step(record.step_id)
            if step.status in {StepStatus.SUCCEEDED, StepStatus.SKIPPED, StepStatus.CANCELLED}:
                continue
            claim = self._claim(step.id, current_time)
            if claim is None:
                continue
            try:
                current = self.repository.get_step_record(step.id)
                current_step = self.repository.get_plan(plan_id).step(step.id)
                await self._cancel_active_run(current, f"{idempotency_key}:run:{step.id}")
                if current_step.status in {
                    StepStatus.PENDING,
                    StepStatus.READY,
                    StepStatus.RUNNING,
                    StepStatus.WAITING,
                }:
                    current_step = current_step.transition_to(StepStatus.CANCELLED)
                updated = replace(
                    current,
                    phase=CoordinationPhase.TERMINAL,
                    retry_due_at=None,
                    wait=None,
                    reconciliation=ReconciliationDisposition.CANONICAL_TERMINAL,
                )
                self.repository.save_step(
                    step=current_step,
                    record=updated,
                    expected_revision=current.revision,
                    claim=claim,
                    now=current_time,
                )
                self._emit(
                    "coordination.step.cancelled",
                    current.task_id,
                    current.plan_id,
                    current.step_id,
                    run_id=current.latest_run_id,
                    outcome=TelemetryOutcome.CANCELLED,
                    attributes={"source": "plan_cancellation"},
                )
            finally:
                self.repository.release_claim(claim)
        task = await self.kernel.get_task(state.plan.task_id)
        if task.status is not TaskStatus.CANCELLED:
            await self.kernel.cancel_task(
                idempotency_key=f"{idempotency_key}:task",
                task_id=state.plan.task_id,
                source="platform-coordinator",
            )
        self._emit("coordination.plan.cancelled", state.plan.task_id, plan_id, None)
        return self.projection(plan_id)

    async def reconcile_plan(
        self,
        plan_id: str,
        *,
        now: datetime | None = None,
    ) -> PlanCoordinationProjection:
        """Reconcile canonical Run/Worker truth before resuming dispatch."""

        current_time = self._now(now)
        state = self.repository.get_plan(plan_id)
        await self.kernel.recover_task(state.plan.task_id)
        for record in self.repository.list_step_records(plan_id):
            if record.phase is not CoordinationPhase.ATTEMPT_ACTIVE:
                continue
            if record.latest_run_id is None:
                await self._mark_inconsistent(
                    record,
                    "active Step has no canonical Run reference",
                    ReconciliationDisposition.MISSING_CANONICAL_RUN,
                    current_time,
                )
                continue
            try:
                run = await self.kernel.get_run(record.task_id, record.latest_run_id)
            except ContractError as exc:
                if exc.code is ErrorCode.NOT_FOUND:
                    await self._mark_inconsistent(
                        record,
                        "referenced canonical Run is missing",
                        ReconciliationDisposition.MISSING_CANONICAL_RUN,
                        current_time,
                    )
                    continue
                raise
            if run.status in _TERMINAL_RUNS:
                await self.observe_run(
                    task_id=record.task_id,
                    run_id=record.latest_run_id,
                    observation_key=f"reconcile:{record.latest_run_id}:{run.status.value}",
                    now=current_time,
                )
            elif run.status is RunStatus.QUEUED and not run.recovery_required:
                await self.kernel.start_run(
                    idempotency_key=self._start_key(record, run.attempt),
                    task_id=record.task_id,
                    run_id=run.run_id,
                    source="platform-coordinator",
                )
                self._emit(
                    "coordination.attempt.dispatched",
                    record.task_id,
                    plan_id,
                    record.step_id,
                    run_id=run.run_id,
                    attributes={"attempt": run.attempt, "reconciled": True},
                )
                self._emit(
                    "coordination.reconciliation.run_started",
                    record.task_id,
                    plan_id,
                    record.step_id,
                    run_id=run.run_id,
                )
        await self.process_due(now=current_time)
        await self.advance(plan_id, now=current_time)
        self._emit("coordination.reconciliation.completed", state.plan.task_id, plan_id, None)
        return self.projection(plan_id)

    async def reconcile_all(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[PlanCoordinationProjection, ...]:
        projections: list[PlanCoordinationProjection] = []
        for state in self.repository.list_active_plans():
            projections.append(await self.reconcile_plan(state.plan.id, now=now))
        return tuple(projections)

    def projection(self, plan_id: str) -> PlanCoordinationProjection:
        state = self.repository.get_plan(plan_id)
        records = {item.step_id: item for item in self.repository.list_step_records(plan_id)}
        return PlanCoordinationProjection(
            task_id=state.plan.task_id,
            plan_id=state.plan.id,
            plan_revision=state.plan.revision,
            steps=tuple(
                StepCoordinationProjection(
                    step_id=step.id,
                    status=step.status,
                    phase=records[step.id].phase,
                    dependency_ids=records[step.id].dependency_ids,
                    satisfied_dependency_ids=records[step.id].satisfied_dependency_ids,
                    latest_run_id=records[step.id].latest_run_id,
                    current_attempt=records[step.id].current_attempt,
                    retry_due_at=records[step.id].retry_due_at,
                    wait_type=(
                        cast(StepWait, records[step.id].wait).wait_type
                        if records[step.id].wait is not None
                        else None
                    ),
                    wait_deadline_at=(
                        cast(StepWait, records[step.id].wait).deadline_at
                        if records[step.id].wait is not None
                        else None
                    ),
                    reconciliation=records[step.id].reconciliation,
                )
                for step in state.steps
            ),
        )

    async def _refresh_dependencies(
        self,
        step: Step,
        record: StepCoordinationRecord,
        by_id: dict[str, Step],
        now: datetime,
    ) -> bool:
        if step.status is not StepStatus.PENDING:
            return False
        satisfied = tuple(
            dependency_id
            for dependency_id in record.dependency_ids
            if by_id[dependency_id].status in _SUCCESSFUL_PREDECESSORS
        )
        failed = tuple(
            dependency_id
            for dependency_id in record.dependency_ids
            if by_id[dependency_id].status in {StepStatus.FAILED, StepStatus.CANCELLED}
        )
        if failed:
            claim = self._claim(step.id, now)
            if claim is None:
                return False
            try:
                current = self.repository.get_step_record(step.id)
                current_step = self.repository.get_plan(step.plan_id).step(step.id)
                if current_step.status is not StepStatus.PENDING:
                    return False
                terminal = current_step.transition_to(
                    StepStatus.CANCELLED
                    if current.predecessor_failure_policy
                    is PredecessorFailurePolicy.CANCEL_DEPENDENT
                    else StepStatus.SKIPPED
                )
                updated = replace(
                    current,
                    phase=CoordinationPhase.TERMINAL,
                    satisfied_dependency_ids=satisfied,
                    reconciliation_detail="predecessor failed or cancelled",
                )
                self.repository.save_step(
                    step=terminal,
                    record=updated,
                    expected_revision=current.revision,
                    claim=claim,
                    now=now,
                )
                self._emit(
                    "coordination.barrier.failed",
                    current.task_id,
                    current.plan_id,
                    current.step_id,
                    outcome=TelemetryOutcome.FAILED,
                    attributes={"failed_predecessors": list(failed)},
                )
                return True
            finally:
                self.repository.release_claim(claim)

        if set(satisfied) != set(record.satisfied_dependency_ids):
            claim = self._claim(step.id, now)
            if claim is None:
                return False
            try:
                current = self.repository.get_step_record(step.id)
                current_step = self.repository.get_plan(step.plan_id).step(step.id)
                updated = replace(current, satisfied_dependency_ids=satisfied)
                if set(satisfied) == set(current.dependency_ids):
                    current_step = current_step.transition_to(StepStatus.READY)
                    updated = replace(updated, phase=CoordinationPhase.READY)
                self.repository.save_step(
                    step=current_step,
                    record=updated,
                    expected_revision=current.revision,
                    claim=claim,
                    now=now,
                )
                self._emit(
                    "coordination.barrier.progress",
                    current.task_id,
                    current.plan_id,
                    current.step_id,
                    attributes={
                        "satisfied": len(satisfied),
                        "required": len(current.dependency_ids),
                    },
                )
                if updated.phase is CoordinationPhase.READY:
                    self._emit(
                        "coordination.barrier.completed",
                        current.task_id,
                        current.plan_id,
                        current.step_id,
                        outcome=TelemetryOutcome.SUCCEEDED,
                        attributes={"required": len(current.dependency_ids)},
                    )
                    self._emit(
                        "coordination.step.ready",
                        current.task_id,
                        current.plan_id,
                        current.step_id,
                    )
                return True
            finally:
                self.repository.release_claim(claim)

        if not record.dependency_ids:
            claim = self._claim(step.id, now)
            if claim is None:
                return False
            try:
                current = self.repository.get_step_record(step.id)
                current_step = self.repository.get_plan(step.plan_id).step(step.id)
                if current_step.status is not StepStatus.PENDING:
                    return False
                ready = current_step.transition_to(StepStatus.READY)
                updated = replace(current, phase=CoordinationPhase.READY)
                self.repository.save_step(
                    step=ready,
                    record=updated,
                    expected_revision=current.revision,
                    claim=claim,
                    now=now,
                )
                self._emit(
                    "coordination.step.ready",
                    current.task_id,
                    current.plan_id,
                    current.step_id,
                )
                return True
            finally:
                self.repository.release_claim(claim)
        return False

    async def _start_attempt(
        self,
        step: Step,
        record: StepCoordinationRecord,
        now: datetime,
    ) -> bool:
        claim = self._claim(step.id, now)
        if claim is None:
            return False
        try:
            current = self.repository.get_step_record(step.id)
            current_step = self.repository.get_plan(step.plan_id).step(step.id)
            if (
                current.phase is not CoordinationPhase.READY
                or current_step.status is not StepStatus.READY
            ):
                return False
            attempt = current.current_attempt + 1
            run = await self.kernel.create_run(
                idempotency_key=self._attempt_key(current, attempt),
                task_id=current.task_id,
                subject_type="step",
                subject_id=current.step_id,
                source="platform-coordinator",
            )
            if run.attempt != attempt:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "kernel Run attempt does not match coordinator attempt",
                    details={"expected_attempt": attempt, "run_attempt": run.attempt},
                )
            self._emit(
                "coordination.attempt.created",
                current.task_id,
                current.plan_id,
                current.step_id,
                run_id=run.run_id,
                attributes={"attempt": attempt},
            )
            running = current_step.transition_to(StepStatus.RUNNING)
            updated = replace(
                current,
                phase=CoordinationPhase.ATTEMPT_ACTIVE,
                latest_run_id=run.run_id,
                current_attempt=attempt,
                retry_due_at=None,
                reconciliation=ReconciliationDisposition.CONSISTENT,
                reconciliation_detail=None,
            )
            self.repository.save_step(
                step=running,
                record=updated,
                expected_revision=current.revision,
                claim=claim,
                now=now,
            )
            await self.kernel.start_run(
                idempotency_key=self._start_key(current, attempt),
                task_id=current.task_id,
                run_id=run.run_id,
                source="platform-coordinator",
            )
            self._emit(
                "coordination.attempt.dispatched",
                current.task_id,
                current.plan_id,
                current.step_id,
                run_id=run.run_id,
                attributes={"attempt": attempt, "reconciled": False},
            )
            self._emit(
                "coordination.run.started",
                current.task_id,
                current.plan_id,
                current.step_id,
                run_id=run.run_id,
                attributes={"attempt": attempt},
            )
            return True
        finally:
            self.repository.release_claim(claim)

    async def _activate_retry(
        self,
        step: Step,
        record: StepCoordinationRecord,
        now: datetime,
    ) -> bool:
        if step.status is not StepStatus.FAILED:
            return False
        claim = self._claim(step.id, now)
        if claim is None:
            return False
        try:
            current = self.repository.get_step_record(step.id)
            current_step = self.repository.get_plan(step.plan_id).step(step.id)
            if (
                current.phase is not CoordinationPhase.RETRY_SCHEDULED
                or current.retry_due_at is None
                or current.retry_due_at > now
            ):
                return False
            ready = current_step.transition_to(StepStatus.READY)
            updated = replace(current, phase=CoordinationPhase.READY, retry_due_at=None)
            self.repository.save_step(
                step=ready,
                record=updated,
                expected_revision=current.revision,
                claim=claim,
                now=now,
            )
            self._emit(
                "coordination.retry.started",
                current.task_id,
                current.plan_id,
                current.step_id,
                attributes={"attempt": current.current_attempt + 1},
            )
            return True
        finally:
            self.repository.release_claim(claim)

    async def _resolve_wait(
        self,
        step_id: str,
        resolution: WaitResolution,
        resolution_key: str,
        now: datetime,
    ) -> PlanCoordinationProjection:
        if not resolution_key.strip():
            raise ValueError("resolution_key must not be blank")
        record = self.repository.get_step_record(step_id)
        if resolution_key in record.processed_keys:
            return self.projection(record.plan_id)
        wait = record.wait
        if wait is None or record.phase is not CoordinationPhase.WAITING:
            raise ContractError(ErrorCode.CONFLICT, "Step has no active durable wait")
        claim = self._required_claim(step_id, now)
        try:
            current = self.repository.get_step_record(step_id)
            if resolution_key in current.processed_keys:
                return self.projection(current.plan_id)
            state = self.repository.get_plan(current.plan_id)
            step = state.step(step_id)
            processed = (*current.processed_keys, resolution_key)
            if resolution is WaitResolution.SATISFIED:
                next_step = step.transition_to(StepStatus.RUNNING)
                phase = CoordinationPhase.ATTEMPT_ACTIVE
                if current.latest_run_id is not None:
                    run = await self.kernel.get_run(current.task_id, current.latest_run_id)
                    if run.status is RunStatus.SUCCEEDED:
                        next_step = next_step.transition_to(StepStatus.SUCCEEDED)
                        phase = CoordinationPhase.TERMINAL
                    elif run.status in {RunStatus.FAILED, RunStatus.TIMED_OUT}:
                        next_step = next_step.transition_to(StepStatus.FAILED)
                        phase = CoordinationPhase.TERMINAL
                    elif run.status is RunStatus.CANCELLED:
                        next_step = next_step.transition_to(StepStatus.CANCELLED)
                        phase = CoordinationPhase.TERMINAL
                updated = replace(
                    current,
                    phase=phase,
                    wait=None,
                    processed_keys=processed,
                )
            elif resolution is WaitResolution.CANCELLED:
                await self._cancel_active_run(current, f"wait:{resolution_key}:cancel")
                next_step = step.transition_to(StepStatus.CANCELLED)
                updated = replace(
                    current,
                    phase=CoordinationPhase.TERMINAL,
                    wait=None,
                    retry_due_at=None,
                    processed_keys=processed,
                )
            else:
                await self._cancel_active_run(current, f"wait:{resolution_key}:fail")
                next_step = step.transition_to(StepStatus.FAILED)
                updated = replace(
                    current,
                    phase=CoordinationPhase.TERMINAL,
                    wait=None,
                    retry_due_at=None,
                    processed_keys=processed,
                )
            self.repository.save_step(
                step=next_step,
                record=updated,
                expected_revision=current.revision,
                claim=claim,
                now=now,
            )
            self._emit(
                "coordination.wait.resolved",
                current.task_id,
                current.plan_id,
                current.step_id,
                run_id=current.latest_run_id,
                outcome=(
                    TelemetryOutcome.SUCCEEDED
                    if resolution is WaitResolution.SATISFIED
                    else TelemetryOutcome.CANCELLED
                    if resolution is WaitResolution.CANCELLED
                    else TelemetryOutcome.FAILED
                ),
                attributes={
                    "wait_type": wait.wait_type.value,
                    "resolution": resolution.value,
                },
            )
        finally:
            self.repository.release_claim(claim)
        await self.advance(record.plan_id, now=now)
        return self.projection(record.plan_id)

    async def _cancel_active_run(self, record: StepCoordinationRecord, key: str) -> None:
        if record.latest_run_id is None:
            return
        run = await self.kernel.get_run(record.task_id, record.latest_run_id)
        if run.status not in _TERMINAL_RUNS:
            await self.kernel.cancel_run(
                idempotency_key=key,
                task_id=record.task_id,
                run_id=record.latest_run_id,
                source="platform-coordinator",
            )

    async def _mark_inconsistent(
        self,
        record: StepCoordinationRecord,
        detail: str,
        disposition: ReconciliationDisposition,
        now: datetime,
    ) -> None:
        claim = self._claim(record.step_id, now)
        if claim is None:
            return
        try:
            current = self.repository.get_step_record(record.step_id)
            step = self.repository.get_plan(record.plan_id).step(record.step_id)
            updated = replace(
                current,
                phase=CoordinationPhase.INCONSISTENT,
                reconciliation=disposition,
                reconciliation_detail=detail,
            )
            self.repository.save_step(
                step=step,
                record=updated,
                expected_revision=current.revision,
                claim=claim,
                now=now,
            )
            self._emit(
                "coordination.reconciliation.inconsistent",
                current.task_id,
                current.plan_id,
                current.step_id,
                run_id=current.latest_run_id,
                outcome=TelemetryOutcome.FAILED,
                attributes={"disposition": disposition.value, "detail": detail},
            )
        finally:
            self.repository.release_claim(claim)

    async def _aggregate_task(self, plan_id: str) -> None:
        state = self.repository.get_plan(plan_id)
        records = self.repository.list_step_records(plan_id)
        if len(records) != len(state.steps) or any(
            record.phase is not CoordinationPhase.TERMINAL for record in records
        ):
            return
        if not state.steps or any(step.status not in _TERMINAL_STEPS for step in state.steps):
            return
        task = await self.kernel.get_task(state.plan.task_id)
        if task.status in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}:
            return
        if any(step.status is StepStatus.FAILED for step in state.steps):
            if task.status in {TaskStatus.RUNNING, TaskStatus.WAITING}:
                await self.kernel.fail_task(
                    idempotency_key=f"coord:{plan_id}:aggregate:failed",
                    task_id=state.plan.task_id,
                    reason="canonical Plan contains a failed Step",
                    source="platform-coordinator",
                )
            return
        if any(step.status is StepStatus.CANCELLED for step in state.steps):
            if task.status in {
                TaskStatus.DRAFT,
                TaskStatus.READY,
                TaskStatus.RUNNING,
                TaskStatus.WAITING,
            }:
                await self.kernel.cancel_task(
                    idempotency_key=f"coord:{plan_id}:aggregate:cancelled",
                    task_id=state.plan.task_id,
                    source="platform-coordinator",
                )
            return
        if task.status is TaskStatus.RUNNING:
            await self.kernel.complete_task(
                idempotency_key=f"coord:{plan_id}:aggregate:succeeded",
                task_id=state.plan.task_id,
                source="platform-coordinator",
            )

    def _claim(self, step_id: str, now: datetime) -> CoordinatorClaim | None:
        claim = self.repository.acquire_claim(
            step_id=step_id,
            owner_id=self.coordinator_id,
            ttl=self.claim_ttl,
            now=now,
        )
        if claim is None:
            record = self.repository.get_step_record(step_id)
            self._emit(
                "coordination.claim.conflict",
                record.task_id,
                record.plan_id,
                record.step_id,
                outcome=TelemetryOutcome.FAILED,
                attributes={"coordinator_id": self.coordinator_id},
            )
        return claim

    def _required_claim(self, step_id: str, now: datetime) -> CoordinatorClaim:
        claim = self._claim(step_id, now)
        if claim is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "another coordinator currently owns this Step",
                details={"step_id": step_id},
            )
        return claim

    @staticmethod
    def _validate_graph(plan: Plan, steps: tuple[Step, ...]) -> None:
        if not steps:
            raise ContractError(ErrorCode.INVALID_REQUEST, "Plan must contain at least one Step")
        ids = {step.id for step in steps}
        if len(ids) != len(steps):
            raise ContractError(ErrorCode.INVALID_REQUEST, "Plan Step IDs must be unique")
        for step in steps:
            if step.plan_id != plan.id:
                raise ContractError(ErrorCode.INVALID_REQUEST, "Step belongs to a different Plan")
            if step.status is not StepStatus.PENDING:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "newly registered Steps must be pending",
                )
            missing = set(step.depends_on) - ids
            if missing:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "Step dependency references an unknown Step",
                    details={
                        "step_id": step.id,
                        "dependencies": cast(JsonValue, sorted(missing)),
                    },
                )
            if step.parent_step_id is not None and step.parent_step_id not in ids:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "parent Step must belong to the same Plan",
                )
        graph = {step.id: step.depends_on for step in steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ContractError(ErrorCode.INVALID_REQUEST, "Plan Step graph contains a cycle")
            if step_id in visited:
                return
            visiting.add(step_id)
            for predecessor in graph[step_id]:
                visit(predecessor)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in ids:
            visit(step_id)

    @staticmethod
    def _validate_wait_scope(
        wait: StepWait,
        plan: Plan,
        step: Step,
        record: StepCoordinationRecord,
    ) -> None:
        if wait.task_id != plan.task_id or wait.plan_id != plan.id or wait.step_id != step.id:
            raise ContractError(ErrorCode.CONFLICT, "wait canonical identity does not match Step")
        if wait.owner_ref != step.owner_ref or wait.project_id != step.project_id:
            raise ContractError(ErrorCode.FORBIDDEN, "wait scope does not match canonical Step")
        if wait.plan_id != record.plan_id or wait.task_id != record.task_id:
            raise ContractError(ErrorCode.CONFLICT, "wait coordination identity mismatch")

    @staticmethod
    def _require_scope(wait: StepWait, owner_ref: OwnerRef, project_id: str | None) -> None:
        if wait.owner_ref != owner_ref or wait.project_id != project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "foreign-scope signal cannot resolve a durable Step wait",
            )

    @staticmethod
    def _attempt_key(record: StepCoordinationRecord, attempt: int) -> str:
        return f"coord:{record.plan_id}:{record.step_id}:attempt:{attempt}"

    @staticmethod
    def _start_key(record: StepCoordinationRecord, attempt: int) -> str:
        return f"coord:{record.plan_id}:{record.step_id}:attempt:{attempt}:start"

    @staticmethod
    def _now(now: datetime | None) -> datetime:
        value = now or datetime.now(UTC)
        if value.tzinfo is None:
            raise ValueError("coordinator timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @staticmethod
    def _run_outcome(status: RunStatus) -> TelemetryOutcome:
        return {
            RunStatus.SUCCEEDED: TelemetryOutcome.SUCCEEDED,
            RunStatus.FAILED: TelemetryOutcome.FAILED,
            RunStatus.CANCELLED: TelemetryOutcome.CANCELLED,
            RunStatus.TIMED_OUT: TelemetryOutcome.TIMED_OUT,
        }.get(status, TelemetryOutcome.UNKNOWN)

    def _emit(
        self,
        event_name: str,
        task_id: str,
        plan_id: str,
        step_id: str | None,
        *,
        run_id: str | None = None,
        outcome: TelemetryOutcome = TelemetryOutcome.UNKNOWN,
        attributes: dict[str, JsonValue] | None = None,
    ) -> None:
        safe_attributes: dict[str, JsonValue] = {"plan_id": plan_id}
        if attributes:
            safe_attributes.update(attributes)
        self.telemetry.timeline(
            event_name=event_name,
            component=FailureComponent.ORCHESTRATION,
            context=TelemetryContext(
                task_id=task_id,
                step_id=step_id,
                run_id=run_id,
                correlation_id=task_id,
            ),
            outcome=outcome,
            attributes=safe_attributes,
        )
