"""Compatibility import for the first-run onboarding workflow.

Canonical implementation lives in :mod:`ai_multi_agent_platform.onboarding.first_run_service`.
New production imports must use that module or the supported package-level exports.
"""

from .first_run_service import *  # noqa: F403
