"""First-run onboarding composition for the general-purpose platform."""

from .agent_lifecycle import (
    FIRST_RUN_AGENT_EXECUTION_PROFILE,
    FIRST_RUN_AGENT_ID_KEY,
    FIRST_RUN_EXECUTION_PROFILE_KEY,
    FIRST_RUN_WORKSPACE_ID_KEY,
    FirstRunAgentLifecycleBackend,
)
from .control_plane import OnboardingResourceService, register_onboarding_control_plane
from .first_task import ONBOARDING_RUN_FIRST_TASK_COMMAND, FirstRunTaskService
from .persistence import (
    ONBOARDING_PROVIDER_SCHEMA_VERSION,
    JsonModelProviderSetupStore,
    JsonOnboardingCommandStore,
    ModelProviderSetupRecord,
    OnboardingCommandRecord,
)
from .providers import OnboardingModelAdapter, OnboardingModelEndpoint
from .readiness import OnboardingService
from .service import (
    FIRST_RUN_RESOURCE_ID,
    ONBOARDING_COLLECTION,
    ONBOARDING_COMMANDS,
    ONBOARDING_CONFIGURE_MODEL_COMMAND,
)

__all__ = [
    "FIRST_RUN_AGENT_EXECUTION_PROFILE",
    "FIRST_RUN_AGENT_ID_KEY",
    "FIRST_RUN_EXECUTION_PROFILE_KEY",
    "FIRST_RUN_RESOURCE_ID",
    "FIRST_RUN_WORKSPACE_ID_KEY",
    "ONBOARDING_COLLECTION",
    "ONBOARDING_COMMANDS",
    "ONBOARDING_CONFIGURE_MODEL_COMMAND",
    "ONBOARDING_PROVIDER_SCHEMA_VERSION",
    "ONBOARDING_RUN_FIRST_TASK_COMMAND",
    "FirstRunAgentLifecycleBackend",
    "FirstRunTaskService",
    "JsonModelProviderSetupStore",
    "JsonOnboardingCommandStore",
    "ModelProviderSetupRecord",
    "OnboardingCommandRecord",
    "OnboardingModelAdapter",
    "OnboardingModelEndpoint",
    "OnboardingResourceService",
    "OnboardingService",
    "register_onboarding_control_plane",
]
