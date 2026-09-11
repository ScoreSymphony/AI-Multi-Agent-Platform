"""Focused Plan registration and graph validation for durable coordination."""

from __future__ import annotations

from typing import Protocol, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import Plan, Step, StepStatus
from ai_multi_agent_platform.kernel.models import TaskState

from .models import (
    CoordinationPhase,
    PredecessorFailurePolicy,
    StepCoordinationRecord,
    StepRetryPolicy,
)
from .repository import CoordinatorRepository


class CanonicalTaskReader(Protocol):
    """Narrow kernel capability required while registering a canonical Plan."""

    async def get_task(self, task_id: str) -> TaskState: ...


class CoordinationRegistration:
    """Validate and persist one canonical Plan projection for the coordinator."""

    def __init__(
        self,
        *,
        repository: CoordinatorRepository,
        kernel: CanonicalTaskReader,
    ) -> None:
        self.repository = repository
        self.kernel = kernel

    async def register(
        self,
        plan: Plan,
        steps: tuple[Step, ...],
        *,
        retry_policies: dict[str, StepRetryPolicy] | None = None,
        predecessor_failure_policy: PredecessorFailurePolicy = PredecessorFailurePolicy.FAIL_FAST,
    ) -> None:
        """Validate canonical identity/graph constraints and persist coordination records."""

        self.validate_graph(plan, steps)
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

    @staticmethod
    def validate_graph(plan: Plan, steps: tuple[Step, ...]) -> None:
        """Reject malformed or cyclic Step graphs before any durable registration write."""

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
