"""Compatibility import for the in-flight #902 branch.

Canonical ownership lives under :mod:`ai_multi_agent_platform.execution.budgets`.  This module is
kept temporarily so the existing #902 tests and integration points can migrate without recreating
a top-level package boundary.
"""

from .execution.budgets import *  # noqa: F403
from .execution.budgets import __all__
