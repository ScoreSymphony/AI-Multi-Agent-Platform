"""Conservative operator repair workflow for inconsistent durable coordination state."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from enum import StrEnum

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import RunStatus, StepStatus

from .models import CoordinationPhase, PlanCoordinationProjection, ReconciliationDisposition
from .service import DurablePlanStepCoordinator

_TERMINAL_RUNS = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.TIMED_OUT}
)
_TERMINAL_STEPS = frozenset(
    {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.CANCELLED}
)


class CoordinatorRepairAction(StrEnum):
    """Narrow repairs that preserve canonical Task/Run truth rather than inventing it."""

    CANCEL_MISSING_RUN = "cancel_missing_run"
    ACKNOWLEDGE_CANONICAL_TERMINAL = "acknowledge_canonical_terminal"


class CoordinatorRepairService:
    """Apply an authorized, revision-checked repair to an explicitly inconsistent Step."""

    def __init__(self, coordinator: DurablePlanStepCoordinator) -> None:
        self.coordinator = coordinator

    async def repair_step(
        self,
        *,
        plan_id: str,
        step_id: str,
        action: CoordinatorRepairAction,
        expected_revision: int,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> PlanCoordinationProjection:
        if expected_revision < 1:
            raise ContractError(ErrorCode.INVALID_REQUEST, "expected_revision must be >= 1")
        if not idempotency_key.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "idempotency key is required")
        current_time = now or datetime.now(UTC)
        if current_time.tzinfo is None:
            raise ValueError("repair time must be timezone-aware")

        repository = self.coordinator.repository
        record = repository.get_step_record(step_id)
        if record.plan_id != plan_id:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"coordination Step {step_id} does not belong to Plan {plan_id}",
            )
        repair_key = f"repair:{idempotency_key}"
        if repair_key in record.processed_keys:
            return self.coordinator.projection(plan_id)
        self._require_repairable(record.phase, record.revision, expected_revision)

        claim = repository.acquire_claim(
            step_id=step_id,
            owner_id=self.coordinator.coordinator_id,
            ttl=self.coordinator.claim_ttl,
            now=current_time,
        )
        if claim is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "another coordinator currently owns this Step",
                details={"step_id": step_id},
            )
        try:
            current = repository.get_step_record(step_id)
            if repair_key in current.processed_keys:
                return self.coordinator.projection(plan_id)
            self._require_repairable(current.phase, current.revision, expected_revision)
            state = repository.get_plan(plan_id)
            step = state.step(step_id)

            if action is CoordinatorRepairAction.CANCEL_MISSING_RUN:
                if current.reconciliation is not ReconciliationDisposition.MISSING_CANONICAL_RUN:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "cancel_missing_run requires a missing-canonical-run disposition",
                    )
                if step.status in _TERMINAL_STEPS:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "canonical Step is already terminal; acknowledge canonical terminal instead",
                    )
                if current.latest_run_id is not None:
                    try:
                        await self.coordinator.kernel.get_run(
                            current.task_id,
                            current.latest_run_id,
                        )
                    except ContractError as exc:
                        if exc.code is not ErrorCode.NOT_FOUND:
                            raise
                    else:
                        raise ContractError(
                            ErrorCode.CONFLICT,
                            "referenced canonical Run now exists; reconcile before repairing",
                        )
                repaired_step = step.transition_to(StepStatus.CANCELLED)
            else:
                if step.status not in _TERMINAL_STEPS:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "acknowledge_canonical_terminal requires a terminal canonical Step",
                    )
                if current.latest_run_id is not None:
                    try:
                        run = await self.coordinator.kernel.get_run(
                            current.task_id,
                            current.latest_run_id,
                        )
                    except ContractError as exc:
                        if exc.code is ErrorCode.NOT_FOUND:
                            raise ContractError(
                                ErrorCode.CONFLICT,
                                "referenced canonical Run is still missing",
                            ) from exc
                        raise
                    if run.status not in _TERMINAL_RUNS:
                        raise ContractError(
                            ErrorCode.CONFLICT,
                            "referenced canonical Run is not terminal",
                        )
                repaired_step = step

            updated = replace(
                current,
                phase=CoordinationPhase.TERMINAL,
                retry_due_at=None,
                wait=None,
                processed_keys=(*current.processed_keys, repair_key),
                reconciliation=ReconciliationDisposition.CANONICAL_TERMINAL,
                reconciliation_detail=f"operator repair applied: {action.value}",
            )
            repository.save_step(
                step=repaired_step,
                record=updated,
                expected_revision=current.revision,
                claim=claim,
                now=current_time,
            )
        finally:
            repository.release_claim(claim)

        # Reuse ordinary coordinator aggregation after the repair. This may cancel/fail/complete
        # the canonical Task, but it never creates a replacement Run for ambiguous missing work.
        await self.coordinator.advance(plan_id, now=current_time)
        return self.coordinator.projection(plan_id)

    @staticmethod
    def _require_repairable(
        phase: CoordinationPhase,
        revision: int,
        expected_revision: int,
    ) -> None:
        if phase is not CoordinationPhase.INCONSISTENT:
            raise ContractError(
                ErrorCode.CONFLICT,
                "operator repair is allowed only for inconsistent coordination state",
            )
        if revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "stale coordinator revision for operator repair",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": revision,
                },
            )


__all__ = ["CoordinatorRepairAction", "CoordinatorRepairService"]
