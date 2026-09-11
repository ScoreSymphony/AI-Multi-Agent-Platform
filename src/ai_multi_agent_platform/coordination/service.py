"""Compatibility import for the durable Plan/Step coordinator implementation.

Canonical implementation lives in
:mod:`ai_multi_agent_platform.coordination.plan_step_coordinator`.
New production imports must use that module or the supported package-level exports.
"""

from .plan_step_coordinator import *  # noqa: F403
