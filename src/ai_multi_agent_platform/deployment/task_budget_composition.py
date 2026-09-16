"""Compose #902 Task execution budgets onto the durable single-node profile."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from ai_multi_agent_platform.accounting import AccountingService
from ai_multi_agent_platform.execution.budgets import (
    SQLiteTaskBudgetStore,
    TaskBudgetCapabilityInvoker,
    TaskBudgetEnforcementService,
    TaskBudgetModelRuntime,
    TaskBudgetPolicyMutationService,
    as_capability_invoker,
    as_model_runtime,
    register_task_budget_control_plane,
)

from .config import SingleNodeConfig
from .durable_connectors import SingleNodeDeployment as DurableSingleNodeDeployment
from .task_budget_bindings import (
    TaskBudgetCoordinationBindings,
    TaskBudgetRepairRuntime,
    as_verification_repair_runtime,
)


@dataclass(slots=True)
class TaskBudgetSingleNodeDeployment(DurableSingleNodeDeployment):
    """Durable public profile extended with canonical #902 budget authorities."""

    task_budgets: TaskBudgetEnforcementService
    task_budget_mutations: TaskBudgetPolicyMutationService


def extend_single_node_with_task_budgets(
    base: DurableSingleNodeDeployment,
    *,
    config: SingleNodeConfig,
    accounting_service: AccountingService,
) -> TaskBudgetSingleNodeDeployment:
    """Attach one durable Task-budget authority to every autonomous execution boundary."""

    task_budgets = TaskBudgetEnforcementService(
        SQLiteTaskBudgetStore(config.database_dir / "task-execution-budgets.sqlite3"),
        accounting_service,
    )
    task_budget_mutations = TaskBudgetPolicyMutationService(
        task_budgets,
        base.approval_gate,
    )
    register_task_budget_control_plane(
        base.control_plane,
        task_budgets,
        task_budget_mutations,
    )

    # Replanning consumes the same Task-level allowance as all other autonomous work.
    base.planning._budget_admission = task_budgets  # noqa: SLF001 - composition seam

    # Coordination remains lifecycle authority for Step attempts. #902 only owns admission,
    # cumulative retry counters and live parallel claims around those lifecycle transitions.
    TaskBudgetCoordinationBindings(base.coordination, task_budgets).install()

    budgeted_models = TaskBudgetModelRuntime(base.model_runtime, task_budgets)
    base.model_runtime = as_model_runtime(budgeted_models)
    base.context.lifecycle._models = base.model_runtime  # noqa: SLF001 - composition seam

    # The canonical Agent capability turn uses the decorated provider boundaries. Disable its
    # older per-turn admission seam in the public composition so a call is never charged twice.
    capability_turn = base.context.lifecycle._capability_turn  # noqa: SLF001 - composition seam
    if capability_turn is not None:
        capability_turn._models = base.model_runtime  # noqa: SLF001 - composition seam
        capability_turn._budget_admission = None  # noqa: SLF001 - avoid duplicate reservations
        capability_turn._invoker = as_capability_invoker(  # noqa: SLF001 - composition seam
            TaskBudgetCapabilityInvoker(capability_turn._invoker, task_budgets)  # noqa: SLF001
        )

    # Verification reviewer model work and repair startup share the same Task authority.
    reviewer_executor = getattr(base.automatic_reviewer, "_executor", None)
    if reviewer_executor is not None and hasattr(reviewer_executor, "_models"):
        reviewer_executor._models = base.model_runtime  # noqa: SLF001

    repair_runtime = getattr(base.automatic_reviewer, "_repair_runtime", None)
    if repair_runtime is not None:
        base.automatic_reviewer._repair_runtime = as_verification_repair_runtime(  # noqa: SLF001
            TaskBudgetRepairRuntime(
                repair_runtime,
                task_budgets,
            )
        )

    base_values: dict[str, Any] = {
        field.name: getattr(base, field.name) for field in fields(DurableSingleNodeDeployment)
    }
    return TaskBudgetSingleNodeDeployment(
        **base_values,
        task_budgets=task_budgets,
        task_budget_mutations=task_budget_mutations,
    )


__all__ = [
    "TaskBudgetSingleNodeDeployment",
    "extend_single_node_with_task_budgets",
]
