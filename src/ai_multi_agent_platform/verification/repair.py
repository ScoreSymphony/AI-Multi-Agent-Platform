"""Canonical repair execution bridge for runtime Verification (#86)."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import TaskStatus, validate_id
from ai_multi_agent_platform.kernel import PlatformKernel

from .gate import VerificationCompletionAuthority
from .models import CompletionState, VerificationOutcome
from .service import VerificationService

_REPAIR_SOURCE = "verification-repair"


@dataclass(frozen=True, slots=True)
class VerificationRepairExecution:
    """Read-only binding returned after a canonical repair Step/Run has started."""

    source_verification_id: str
    task_id: str
    plan_id: str
    step_id: str
    run_id: str
    repair_attempt: int

    def __post_init__(self) -> None:
        validate_id(self.source_verification_id, "verification")
        validate_id(self.task_id, "task")
        validate_id(self.plan_id, "plan")
        validate_id(self.step_id, "step")
        validate_id(self.run_id, "run")
        if self.repair_attempt < 1:
            raise ValueError("repair_attempt must be >= 1")


class VerificationRepairRuntime:
    """Translate ``needs_changes`` into ordinary canonical Plan/Step/Run execution.

    Verification remains the authority for repair budget and exact-subject acceptance.
    The kernel remains the authority for Task/Run lifecycle. This bridge only coordinates
    those two existing authorities; it owns no private task status or execution history.
    """

    def __init__(
        self,
        verification: VerificationService,
        completion: VerificationCompletionAuthority,
        kernel: PlatformKernel,
    ) -> None:
        self._verification = verification
        self._completion = completion
        self._kernel = kernel

    async def start_repair(
        self,
        verification_id: str,
        *,
        idempotency_key: str,
        step_id: str | None = None,
        actor_ref: str | None = None,
    ) -> VerificationRepairExecution:
        """Create and start one canonical repair Step/Run for the current failed review.

        A single-step replan is selected automatically. If a replaceable orchestrator
        proposes multiple repair steps, the caller/scheduler must explicitly select the
        step to start rather than this bridge inventing orchestration policy.
        """

        validate_id(verification_id, "verification")
        if not idempotency_key.strip():
            raise ValueError("repair idempotency_key must not be blank")

        request = self._verification.get_request(verification_id)
        result = self._verification.result_for(verification_id)
        if result is None or result.outcome is not VerificationOutcome.NEEDS_CHANGES:
            raise ContractError(
                ErrorCode.CONFLICT,
                "repair execution requires a completed needs_changes verification",
            )

        decision = self._completion.assess_task_completion(request.task_id)
        if (
            decision.state is not CompletionState.REPAIR_REQUIRED
            or decision.subject != request.subject
            or verification_id not in decision.blocking_verification_ids
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "verification is not the current canonical repair requirement",
            )

        policy = self._verification.get_policy(request.policy_id, request.policy_version)
        repair_attempt = request.repair_attempt + 1
        if repair_attempt > policy.max_repair_attempts:
            raise ContractError(ErrorCode.CONFLICT, "verification repair limit exhausted")

        task = await self._kernel.get_task(request.task_id)
        if task.status not in {TaskStatus.WAITING, TaskStatus.RUNNING}:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task cannot start verification repair from {task.status.value}",
            )
        if task.status is TaskStatus.WAITING and (
            task.wait_reason != "verification:repair_required" or not task.blocked
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "waiting task is not canonically blocked for verification repair",
            )

        key = f"verification-repair:{verification_id}:{repair_attempt}"
        existing = await self._existing_execution(
            request.task_id, verification_id, repair_attempt, key
        )
        if existing is not None:
            return existing
        planned = await self._kernel.plan_task(
            idempotency_key=f"{key}:plan",
            task_id=request.task_id,
            actor_ref=actor_ref,
            source=_REPAIR_SOURCE,
        )
        if planned.plan_ref is None or not planned.step_ids:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repair planning produced no canonical executable step",
            )

        selected_step = step_id
        if selected_step is None:
            if len(planned.step_ids) != 1:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "repair plan has multiple steps; an explicit step_id is required",
                )
            selected_step = planned.step_ids[0]
        validate_id(selected_step, "step")
        if selected_step not in planned.step_ids:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "selected repair step does not belong to the current canonical repair plan",
            )

        if planned.status is TaskStatus.WAITING:
            active_task = await self._kernel.resume_task(
                idempotency_key=f"{key}:resume",
                task_id=request.task_id,
                actor_ref=actor_ref,
                source=_REPAIR_SOURCE,
            )
        elif planned.status is TaskStatus.RUNNING:
            active_task = planned
        else:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"repair plan cannot execute while task is {planned.status.value}",
            )
        if active_task.status is not TaskStatus.RUNNING:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repair task did not enter canonical running state",
            )

        run = await self._kernel.create_run(
            idempotency_key=f"{key}:create-run",
            task_id=request.task_id,
            subject_type="step",
            subject_id=selected_step,
            actor_ref=actor_ref,
            source=_REPAIR_SOURCE,
        )
        started = await self._kernel.start_run(
            idempotency_key=f"{key}:start-run",
            task_id=request.task_id,
            run_id=run.run_id,
            actor_ref=actor_ref,
            source=_REPAIR_SOURCE,
        )

        return VerificationRepairExecution(
            source_verification_id=verification_id,
            task_id=request.task_id,
            plan_id=planned.plan_ref,
            step_id=selected_step,
            run_id=started.run_id,
            repair_attempt=repair_attempt,
        )

    async def _existing_execution(
        self,
        task_id: str,
        verification_id: str,
        repair_attempt: int,
        key: str,
    ) -> VerificationRepairExecution | None:
        matches = [
            event
            for event in await self._kernel.history(task_id)
            if event.event_type == "run.created"
            and event.provenance is not None
            and event.provenance.source == _REPAIR_SOURCE
            and event.causation_id == f"{key}:create-run"
        ]
        if len(matches) > 1:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "one verification repair round has multiple canonical repair runs",
            )
        if not matches:
            return None
        event = matches[0]
        plan_id = event.payload.get("plan_ref")
        step_id = event.payload.get("subject_id")
        if not isinstance(plan_id, str) or not isinstance(step_id, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical repair run is missing plan/step provenance",
            )
        return VerificationRepairExecution(
            source_verification_id=verification_id,
            task_id=task_id,
            plan_id=plan_id,
            step_id=step_id,
            run_id=event.subject_id,
            repair_attempt=repair_attempt,
        )
