"""Per-Task autonomous execution budget enforcement.

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
    from .control_plane import TASK_BUDGET_COLLECTION as TASK_BUDGET_COLLECTION
    from .control_plane import TASK_BUDGET_COMMANDS as TASK_BUDGET_COMMANDS
    from .control_plane import TASK_BUDGET_CONFIGURE_COMMAND as TASK_BUDGET_CONFIGURE_COMMAND
    from .control_plane import TASK_BUDGET_MODULE as TASK_BUDGET_MODULE
    from .control_plane import TASK_BUDGET_REVISE_COMMAND as TASK_BUDGET_REVISE_COMMAND
    from .control_plane import TaskBudgetCommandHandlers as TaskBudgetCommandHandlers
    from .control_plane import TaskBudgetResourceService as TaskBudgetResourceService
    from .control_plane import (
        register_task_budget_control_plane as register_task_budget_control_plane,
    )
    from .governance import TaskBudgetPolicyMutationService as TaskBudgetPolicyMutationService
    from .models import BudgetActionKind as BudgetActionKind
    from .models import BudgetAdmissionDecision as BudgetAdmissionDecision
    from .models import BudgetAdmissionOutcome as BudgetAdmissionOutcome
    from .models import BudgetConsumption as BudgetConsumption
    from .models import BudgetConsumptionSource as BudgetConsumptionSource
    from .models import BudgetDimension as BudgetDimension
    from .models import BudgetDimensionSnapshot as BudgetDimensionSnapshot
    from .models import BudgetExhaustionAction as BudgetExhaustionAction
    from .models import BudgetReservation as BudgetReservation
    from .models import ReservationState as ReservationState
    from .models import TaskBudgetLimit as TaskBudgetLimit
    from .models import TaskBudgetPolicy as TaskBudgetPolicy
    from .models import TaskBudgetSnapshot as TaskBudgetSnapshot
    from .models import UnavailableMetricPolicy as UnavailableMetricPolicy
    from .runtime import TaskBudgetCapabilityInvoker as TaskBudgetCapabilityInvoker
    from .runtime import TaskBudgetModelRuntime as TaskBudgetModelRuntime
    from .runtime import as_capability_invoker as as_capability_invoker
    from .runtime import as_model_runtime as as_model_runtime
    from .service import TaskBudgetAdmission as TaskBudgetAdmission
    from .service import TaskBudgetEnforcementService as TaskBudgetEnforcementService
    from .store import InMemoryTaskBudgetStore as InMemoryTaskBudgetStore
    from .store import ReservationClaim as ReservationClaim
    from .store import SQLiteTaskBudgetStore as SQLiteTaskBudgetStore
    from .store import TaskBudgetStore as TaskBudgetStore


__all__ = sorted(_EXPORT_MODULES)
