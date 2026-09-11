"""Compatibility imports for the responsibility-specific planning façade.

Canonical implementation lives in :mod:`ai_multi_agent_platform.planning.planning_facade`.
New production imports must use that module or the supported package-level exports.
"""

from .planning_facade import ActivatedPlanCoordinator as ActivatedPlanCoordinator
from .planning_facade import PlanningEventSink as PlanningEventSink
from .planning_facade import PlanningKernel as PlanningKernel
from .planning_facade import PlanningService as PlanningService

__all__ = [
    "ActivatedPlanCoordinator",
    "PlanningEventSink",
    "PlanningKernel",
    "PlanningService",
]
