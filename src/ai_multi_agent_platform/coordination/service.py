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

from .attempt_outcomes import CoordinationAttemptOutcomes
from .models import (
    ApprovalOutcome,
    CoordinationPhase,
    CoordinatorClaim,
    PlanCoordinationProjection,
    PredecessorFailurePolicy,
    ReconciliationDisposition,
    RetryState,
    StepCoordinationProjection,
    StepCoordinationRecord,
    StepRetryPolicy,
    StepWait,
    WaitResolution,
    WaitType,
)
from .progression import CoordinationProgression
from .registration import CoordinationRegistration
from .repository import CoordinatorRepository
from .waits import CoordinationWaits


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
        self._registration = CoordinationRegistration(repository=repository, kernel=kernel)
        self._progression = CoordinationProgression(repository=repository, kernel=kernel)
        self._waits = CoordinationWaits(repository=repository, kernel=kernel)
        self._attempt_outcomes = CoordinationAttemptOutcomes(repository=repository, kernel=kernel)

    async def register_plan(
        self,
        plan: Plan,
        steps: tuple[Step, ...],
        *,
        retry_policies: dict[str, StepRetryPolicy] | None = None,
        predecessor_failure_policy: PredecessorFailurePolicy = PredecessorFailurePolicy.FAIL_FAST,
    ) -> PlanCoordinationProjection:
        """Register the exact active canonical Plan already present in kernel truth."""

        await self._registration.register(
            plan,
            steps,
            retry_policies=retry_policies,
            predecessor_failure_policy=predecessor_failure_policy,
        )
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
        result = await self._attempt_outcomes.observe_run(
            task_id=task_id,
            run_id=run_id,
            failure_category=failure_category,
            observation_key=observation_key,
            now=current_time,
            claim=self._claim,
            close_wait=self._close_wait,
            emit=self._emit,
        )
        if result.changed:
            await self.advance(result.plan_id, now=current_time)
        return self.projection(result.plan_id)

    async def wait_step(
        self,
        wait: StepWait,
        *,
        now: datetime | None = None,
    ) -> PlanCoordinationProjection:
        """Persist a backend-neutral wait without raw provider payloads or secrets."""

        current_time = self._now(now)
        result = await self._waits.wait_step(
            wait,
            current_time,
            required_claim=self._required_claim,
            emit=self._emit,
        )
        return self.projection(result.plan_id)

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
        current_time = self._now(now)
        result = await self._waits.resolve_approval(
            step_id=step_id,
            approval_id=approval_id,
            subject_type=subject_type,
            subject_id=subject_id,
            action=action,
            outcome=outcome,
            resolution_key=resolution_key,
            owner_ref=owner_ref,
            project_id=project_id,
            now=current_time,
            required_claim=self._required_claim,
            cancel_active_run=self._cancel_active_run,
            emit=self._emit,
        )
        if result.changed:
            await self.advance(result.plan_id, now=current_time)
        return self.projection(result.plan_id)

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

        current_time = self._now(now)
        result = await self._waits.resolve_event(
            step_id=step_id,
            event_id=event_id,
            event_type=event_type,
            correlation_key=correlation_key,
            owner_ref=owner_ref,
            project_id=project_id,
            now=current_time,
            required_claim=self._required_claim,
            cancel_active_run=self._cancel_active_run,
            emit=self._emit,
        )
        if result.changed:
            await self.advance(result.plan_id, now=current_time)
        return self.projection(result.plan_id)

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

        current_time = self._now(now)
        result = await self._waits.resolve_external_job(
            step_id=step_id,
            external_job_ref=external_job_ref,
            resolution=resolution,
            resolution_key=resolution_key,
            owner_ref=owner_ref,
            project_id=project_id,
            now=current_time,
            required_claim=self._required_claim,
            cancel_active_run=self._cancel_active_run,
            emit=self._emit,
        )
        if result.changed:
            await self.advance(result.plan_id, now=current_time)
        return self.projection(result.plan_id)

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
                retry_state = (
                    RetryState.CANCELLED
                    if current.retry_state in {RetryState.SCHEDULED, RetryState.ACTIVE}
                    else current.retry_state
                )
                updated = replace(
                    current,
                    phase=CoordinationPhase.TERMINAL,
                    retry_due_at=None,
                    retry_state=retry_state,
                    wait=self._close_wait(
                        current.wait,
                        resolution=WaitResolution.CANCELLED,
                        resolution_key=f"{idempotency_key}:wait:{step.id}",
                        now=current_time,
                    ),
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
        return await self._progression.refresh_dependencies(
            step,
            record,
            by_id,
            now,
            claim=self._claim,
            emit=self._emit,
        )

    async def _start_attempt(
        self,
        step: Step,
        record: StepCoordinationRecord,
        now: datetime,
    ) -> bool:
        return await self._progression.start_attempt(
            step,
            record,
            now,
            claim=self._claim,
            emit=self._emit,
        )

    async def _activate_retry(
        self,
        step: Step,
        record: StepCoordinationRecord,
        now: datetime,
    ) -> bool:
        return await self._attempt_outcomes.activate_retry(
            step,
            record,
            now,
            claim=self._claim,
            emit=self._emit,
        )

    async def _resolve_wait(
        self,
        step_id: str,
        resolution: WaitResolution,
        resolution_key: str,
        now: datetime,
    ) -> PlanCoordinationProjection:
        result = await self._waits.resolve(
            step_id,
            resolution,
            resolution_key,
            now,
            required_claim=self._required_claim,
            cancel_active_run=self._cancel_active_run,
            emit=self._emit,
        )
        if result.changed:
            await self.advance(result.plan_id, now=now)
        return self.projection(result.plan_id)

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
        if task.plan_ref != plan_id:
            return
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
        CoordinationRegistration.validate_graph(plan, steps)

    @staticmethod
    def _validate_wait_scope(
        wait: StepWait,
        plan: Plan,
        step: Step,
        record: StepCoordinationRecord,
    ) -> None:
        CoordinationWaits.validate_wait_scope(wait, plan, step, record)

    @staticmethod
    def _require_scope(wait: StepWait, owner_ref: OwnerRef, project_id: str | None) -> None:
        CoordinationWaits.require_scope(wait, owner_ref, project_id)

    @staticmethod
    def _close_wait(
        wait: StepWait | None,
        *,
        resolution: WaitResolution,
        resolution_key: str,
        now: datetime,
    ) -> StepWait | None:
        return CoordinationWaits.close_wait(
            wait,
            resolution=resolution,
            resolution_key=resolution_key,
            now=now,
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
