"""Compatibility imports for the first-run onboarding workflow.

Canonical implementation lives in :mod:`ai_multi_agent_platform.onboarding.first_run_service`.
New production imports must use that module or the supported package-level exports.
"""

from .first_run_service import (
    FIRST_RUN_RESOURCE_ID as FIRST_RUN_RESOURCE_ID,
    ONBOARDING_COLLECTION as ONBOARDING_COLLECTION,
    ONBOARDING_COMMANDS as ONBOARDING_COMMANDS,
    ONBOARDING_CONFIGURE_MODEL_COMMAND as ONBOARDING_CONFIGURE_MODEL_COMMAND,
    FirstRunPath as FirstRunPath,
    FirstRunPathProjection as FirstRunPathProjection,
    OnboardingService as OnboardingService,
)

__all__ = [
    "FIRST_RUN_RESOURCE_ID",
    "ONBOARDING_COLLECTION",
    "ONBOARDING_COMMANDS",
    "ONBOARDING_CONFIGURE_MODEL_COMMAND",
    "FirstRunPath",
    "FirstRunPathProjection",
    "OnboardingService",
]
