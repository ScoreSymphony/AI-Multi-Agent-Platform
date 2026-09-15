"""Deployment-level #902 bindings for autonomous runtimes owned by other domains."""

from __future__ import annotations

from ai_multi_agent_platform.execution.budgets import (
    BudgetActionKind,
    BudgetDimension,
    TaskBudgetEnforcementService,
)
from ai_multi_agent_platform.verification.repair import (
    VerificationRepairExecution,
    VerificationRepairRuntime,
)


class TaskBudgetRepairRuntime:
    """Enforce one shared Task repair budget before a new repair Run can start.

    Verification remains the authority for whether repair is required and for its own policy-level
    repair cap. This decorator only contributes the cross-runtime Task budget from #902. Existing
    idempotent repair executions are returned without consuming another Task-budget unit.
    """

    def __init__(
        self,
        inner: VerificationRepairRuntime,
        budgets: TaskBudgetEnforcementService,
    ) -> None:
        self._inner = inner
        self._budgets = budgets

    async def start_repair(
        self,
        verification_id: str,
        *,
        idempotency_key: str,
        step_id: str | None = None,
        actor_ref: str | None = None,
    ) -> VerificationRepairExecution:
        request = await self._inner._runtime_verification.get_request(  # noqa: SLF001
            verification_id
        )
        repair_attempt = request.repair_attempt + 1
        canonical_key = f"verification-repair:{verification_id}:{repair_attempt}"
        existing = await self._inner._existing_execution(  # noqa: SLF001
            request.task_id,
            verification_id,
            repair_attempt,
            canonical_key,
        )
        if existing is not None:
            return existing

        decision = await self._budgets.admit(
            task_id=request.task_id,
            action=BudgetActionKind.REPAIR,
            quantities={BudgetDimension.REPAIRS: 1.0},
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            causation_id=verification_id,
            provenance={
                "enforcement_point": "verification_repair_runtime",
                "verification_id": verification_id,
                "repair_attempt": repair_attempt,
            },
        )
        await self._budgets.require_permitted(decision)
        try:
            execution = await self._inner.start_repair(
                verification_id,
                idempotency_key=idempotency_key,
                step_id=step_id,
                actor_ref=actor_ref,
            )
        except BaseException:
            await self._budgets.release(decision)
            raise

        post_action = await self._budgets.reconcile(decision)
        await self._budgets.require_permitted(post_action)
        return execution


__all__ = ["TaskBudgetRepairRuntime"]
