"""Per-Task autonomous execution budget enforcement for Issue #902.

Keep this package boundary intentionally lazy. Runtime owners such as ``agents`` import narrow
budget submodules during their own package initialization; eagerly importing the Control Plane or
security integration here would pull Context back into ``agents`` and create an import cycle.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_EXPORT_MODULES = {
    "BudgetActionKind": ".models",
    "BudgetAdmissionDecision": ".models",
    "BudgetAdmissionOutcome": ".models",
    "BudgetConsumption": ".models",
    "BudgetConsumptionSource": ".models",
    "BudgetDimension": ".models",
    "BudgetDimensionSnapshot": ".models",
    "BudgetExhaustionAction": ".models",
    "BudgetReservation": ".models",
    "ReservationState": ".models",
    "TaskBudgetLimit": ".models",
    "TaskBudgetPolicy": ".models",
    "TaskBudgetSnapshot": ".models",
    "UnavailableMetricPolicy": ".models",
    "TaskBudgetAdmission": ".service",
    "TaskBudgetEnforcementService": ".service",
    "InMemoryTaskBudgetStore": ".store",
    "ReservationClaim": ".store",
    "SQLiteTaskBudgetStore": ".store",
    "TaskBudgetStore": ".store",
    "TaskBudgetCapabilityInvoker": ".runtime",
    "TaskBudgetModelRuntime": ".runtime",
    "as_capability_invoker": ".runtime",
    "as_model_runtime": ".runtime",
    "TaskBudgetPolicyMutationService": ".governance",
    "TASK_BUDGET_MODULE": ".control_plane",
    "TASK_BUDGET_COLLECTION": ".control_plane",
    "TASK_BUDGET_COMMANDS": ".control_plane",
    "TASK_BUDGET_CONFIGURE_COMMAND": ".control_plane",
    "TASK_BUDGET_REVISE_COMMAND": ".control_plane",
    "TaskBudgetCommandHandlers": ".control_plane",
    "TaskBudgetResourceService": ".control_plane",
    "register_task_budget_control_plane": ".control_plane",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORT_MODULES))


if TYPE_CHECKING:
    from .control_plane import (
        TASK_BUDGET_COLLECTION,
        TASK_BUDGET_COMMANDS,
        TASK_BUDGET_CONFIGURE_COMMAND,
        TASK_BUDGET_MODULE,
        TASK_BUDGET_REVISE_COMMAND,
        TaskBudgetCommandHandlers,
        TaskBudgetResourceService,
        register_task_budget_control_plane,
    )
    from .governance import TaskBudgetPolicyMutationService
    from .models import (
        BudgetActionKind,
        BudgetAdmissionDecision,
        BudgetAdmissionOutcome,
        BudgetConsumption,
        BudgetConsumptionSource,
        BudgetDimension,
        BudgetDimensionSnapshot,
        BudgetExhaustionAction,
        BudgetReservation,
        ReservationState,
        TaskBudgetLimit,
        TaskBudgetPolicy,
        TaskBudgetSnapshot,
        UnavailableMetricPolicy,
    )
    from .runtime import (
        TaskBudgetCapabilityInvoker,
        TaskBudgetModelRuntime,
        as_capability_invoker,
        as_model_runtime,
    )
    from .service import TaskBudgetAdmission, TaskBudgetEnforcementService
    from .store import (
        InMemoryTaskBudgetStore,
        ReservationClaim,
        SQLiteTaskBudgetStore,
        TaskBudgetStore,
    )


__all__ = sorted(_EXPORT_MODULES)
