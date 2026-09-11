"""Durable Step waits and canonical wait-signal resolution."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, Plan, RunStatus, Step, StepStatus
from ai_multi_agent_platform.kernel.models import RunState
from ai_multi_agent_platform.observability import TelemetryOutcome

from .models import (
    ApprovalOutcome,
    CoordinationPhase,
    CoordinatorClaim,
    RetryState,
    StepCoordinationRecord,
    StepWait,
    WaitResolution,
    WaitType,
)
from .repository import CoordinatorRepository


class WaitRunKernel(Protocol):
    """Narrow canonical Run read capability required by wait resolution."""

    async def get_run(self, task_id: str, run_id: str) -> RunState: ...


class RequiredClaimProvider(Protocol):
    def __call__(self, step_id: str, now: datetime) -> CoordinatorClaim: ...


class CancelActiveRun(Protocol):
    async def __call__(self, record: StepCoordinationRecord, key: str) -> None: ...


class WaitEmitter(Protocol):
    def __call__(
        self,
        event_name: str,
        task_id: str,
        plan_id: str,
        step_id: str | None,
        *,
        run_id: str | None = None,
        outcome: TelemetryOutcome = TelemetryOutcome.UNKNOWN,
        attributes: dict[str, JsonValue] | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class WaitMutation:
    """Internal wait mutation result used by the coordinator façade."""

    plan_id: str
    changed: bool


class CoordinationWaits:
    """Own durable wait persistence, validation and signal resolution."""

    def __init__(
        self,
        *,
        repository: CoordinatorRepository,
        kernel: WaitRunKernel,
    ) -> None:
        self.repository = repository
        self.kernel = kernel

    async def wait_step(
        self,
        wait: StepWait,
        now: datetime,
        *,
        required_claim: RequiredClaimProvider,
        emit: WaitEmitter,
    ) -> WaitMutation:
        state = self.repository.get_plan(wait.plan_id)
        step = state.step(wait.step_id)
        record = self.repository.get_step_record(wait.step_id)
        self.validate_wait_scope(wait, state.plan, step, record)
        if record.wait is not None:
            if record.wait.wait_key == wait.wait_key:
                return WaitMutation(plan_id=wait.plan_id, changed=False)
            if not record.wait.resolved:
                raise ContractError(ErrorCode.CONFLICT, "Step already has a different durable wait")
        if (
            step.status is not StepStatus.RUNNING
            or record.phase is not CoordinationPhase.ATTEMPT_ACTIVE
        ):
            raise ContractError(ErrorCode.CONFLICT, "only an active running Step can enter a wait")
        claim = required_claim(step.id, now)
        try:
            waiting = step.transition_to(StepStatus.WAITING)
            updated = replace(record, phase=CoordinationPhase.WAITING, wait=wait)
            self.repository.save_step(
                step=waiting,
                record=updated,
                expected_revision=record.revision,
                claim=claim,
                now=now,
            )
        finally:
            self.repository.release_claim(claim)
        emit(
            "coordination.wait.created",
            record.task_id,
            record.plan_id,
            record.step_id,
            run_id=record.latest_run_id,
            attributes={"wait_type": wait.wait_type.value},
        )
        return WaitMutation(plan_id=wait.plan_id, changed=True)

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
        now: datetime,
        required_claim: RequiredClaimProvider,
        cancel_active_run: CancelActiveRun,
        emit: WaitEmitter,
    ) -> WaitMutation:
        record = self.repository.get_step_record(step_id)
        if resolution_key in record.processed_keys:
            return WaitMutation(plan_id=record.plan_id, changed=False)
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
        self.require_scope(wait, owner_ref, project_id)
        resolution = {
            "approved": WaitResolution.SATISFIED,
            "rejected": WaitResolution.REJECTED,
            "expired": WaitResolution.EXPIRED,
            "cancelled": WaitResolution.CANCELLED,
        }[outcome]
        return await self.resolve(
            step_id,
            resolution,
            resolution_key,
            now,
            required_claim=required_claim,
            cancel_active_run=cancel_active_run,
            emit=emit,
        )

    async def resolve_event(
        self,
        *,
        step_id: str,
        event_id: str,
        event_type: str,
        correlation_key: str,
        owner_ref: OwnerRef,
        project_id: str | None,
        now: datetime,
        required_claim: RequiredClaimProvider,
        cancel_active_run: CancelActiveRun,
        emit: WaitEmitter,
    ) -> WaitMutation:
        if not event_id.strip():
            raise ValueError("event_id must not be blank")
        resolution_key = f"event:{event_id}"
        record = self.repository.get_step_record(step_id)
        if resolution_key in record.processed_keys:
            return WaitMutation(plan_id=record.plan_id, changed=False)
        wait = record.wait
        if wait is None or wait.wait_type is not WaitType.EVENT:
            raise ContractError(ErrorCode.CONFLICT, "Step is not waiting for a canonical Event")
        self.require_scope(wait, owner_ref, project_id)
        if wait.event_type != event_type or wait.correlation_key != correlation_key:
            raise ContractError(ErrorCode.CONFLICT, "Event type/correlation does not match wait")
        return await self.resolve(
            step_id,
            WaitResolution.SATISFIED,
            resolution_key,
            now,
            required_claim=required_claim,
            cancel_active_run=cancel_active_run,
            emit=emit,
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
        now: datetime,
        required_claim: RequiredClaimProvider,
        cancel_active_run: CancelActiveRun,
        emit: WaitEmitter,
    ) -> WaitMutation:
        record = self.repository.get_step_record(step_id)
        if resolution_key in record.processed_keys:
            return WaitMutation(plan_id=record.plan_id, changed=False)
        wait = record.wait
        if wait is None or wait.wait_type is not WaitType.EXTERNAL_JOB:
            raise ContractError(ErrorCode.CONFLICT, "Step is not waiting for an external job")
        self.require_scope(wait, owner_ref, project_id)
        if wait.external_job_ref != external_job_ref:
            raise ContractError(ErrorCode.CONFLICT, "external job reference does not match wait")
        return await self.resolve(
            step_id,
            resolution,
            resolution_key,
            now,
            required_claim=required_claim,
            cancel_active_run=cancel_active_run,
            emit=emit,
        )

    async def resolve(
        self,
        step_id: str,
        resolution: WaitResolution,
        resolution_key: str,
        now: datetime,
        *,
        required_claim: RequiredClaimProvider,
        cancel_active_run: CancelActiveRun,
        emit: WaitEmitter,
    ) -> WaitMutation:
        if not resolution_key.strip():
            raise ValueError("resolution_key must not be blank")
        record = self.repository.get_step_record(step_id)
        if resolution_key in record.processed_keys:
            return WaitMutation(plan_id=record.plan_id, changed=False)
        wait = record.wait
        if wait is None or wait.resolved or record.phase is not CoordinationPhase.WAITING:
            raise ContractError(ErrorCode.CONFLICT, "Step has no active durable wait")
        claim = required_claim(step_id, now)
        try:
            current = self.repository.get_step_record(step_id)
            if resolution_key in current.processed_keys:
                return WaitMutation(plan_id=current.plan_id, changed=False)
            state = self.repository.get_plan(current.plan_id)
            step = state.step(step_id)
            processed = (*current.processed_keys, resolution_key)
            resolved_wait = self.close_wait(
                wait,
                resolution=resolution,
                resolution_key=resolution_key,
                now=now,
            )
            if resolution is WaitResolution.SATISFIED:
                next_step = step.transition_to(StepStatus.RUNNING)
                phase = CoordinationPhase.ATTEMPT_ACTIVE
                retry_state = current.retry_state
                if current.latest_run_id is not None:
                    run = await self.kernel.get_run(current.task_id, current.latest_run_id)
                    if run.status is RunStatus.SUCCEEDED:
                        next_step = next_step.transition_to(StepStatus.SUCCEEDED)
                        phase = CoordinationPhase.TERMINAL
                        if retry_state is RetryState.ACTIVE:
                            retry_state = RetryState.COMPLETED
                    elif run.status in {RunStatus.FAILED, RunStatus.TIMED_OUT}:
                        next_step = next_step.transition_to(StepStatus.FAILED)
                        phase = CoordinationPhase.TERMINAL
                        if retry_state is RetryState.ACTIVE:
                            retry_state = RetryState.NOT_RETRYABLE
                    elif run.status is RunStatus.CANCELLED:
                        next_step = next_step.transition_to(StepStatus.CANCELLED)
                        phase = CoordinationPhase.TERMINAL
                        if retry_state is RetryState.ACTIVE:
                            retry_state = RetryState.CANCELLED
                updated = replace(
                    current,
                    phase=phase,
                    wait=resolved_wait,
                    retry_state=retry_state,
                    processed_keys=processed,
                )
            elif resolution is WaitResolution.CANCELLED:
                await cancel_active_run(current, f"wait:{resolution_key}:cancel")
                next_step = step.transition_to(StepStatus.CANCELLED)
                retry_state = (
                    RetryState.CANCELLED
                    if current.retry_state in {RetryState.SCHEDULED, RetryState.ACTIVE}
                    else current.retry_state
                )
                updated = replace(
                    current,
                    phase=CoordinationPhase.TERMINAL,
                    wait=resolved_wait,
                    retry_due_at=None,
                    retry_state=retry_state,
                    processed_keys=processed,
                )
            else:
                await cancel_active_run(current, f"wait:{resolution_key}:fail")
                next_step = step.transition_to(StepStatus.FAILED)
                retry_state = (
                    RetryState.NOT_RETRYABLE
                    if current.retry_state in {RetryState.SCHEDULED, RetryState.ACTIVE}
                    else current.retry_state
                )
                updated = replace(
                    current,
                    phase=CoordinationPhase.TERMINAL,
                    wait=resolved_wait,
                    retry_due_at=None,
                    retry_state=retry_state,
                    processed_keys=processed,
                )
            self.repository.save_step(
                step=next_step,
                record=updated,
                expected_revision=current.revision,
                claim=claim,
                now=now,
            )
            emit(
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
            return WaitMutation(plan_id=current.plan_id, changed=True)
        finally:
            self.repository.release_claim(claim)

    @staticmethod
    def validate_wait_scope(
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
    def require_scope(wait: StepWait, owner_ref: OwnerRef, project_id: str | None) -> None:
        if wait.owner_ref != owner_ref or wait.project_id != project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "foreign-scope signal cannot resolve a durable Step wait",
            )

    @staticmethod
    def close_wait(
        wait: StepWait | None,
        *,
        resolution: WaitResolution,
        resolution_key: str,
        now: datetime,
    ) -> StepWait | None:
        if wait is None or wait.resolved:
            return wait
        return replace(
            wait,
            resolved_at=now,
            resolution=resolution,
            resolution_key=resolution_key,
        )
