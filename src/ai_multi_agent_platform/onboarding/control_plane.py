"""Control Plane registration for the first-run onboarding composition."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import CommandHandler, ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .component_setup import (
    COMPONENT_SETUP_RESOURCE_ID,
    ONBOARDING_SAVE_COMPONENT_PROFILE_COMMAND,
    ONBOARDING_SELECT_COMPONENT_PROFILE_COMMAND,
    OnboardingComponentSetupService,
)
from .first_task import ONBOARDING_RUN_FIRST_TASK_COMMAND, FirstRunTaskService
from .service import (
    FIRST_RUN_RESOURCE_ID,
    ONBOARDING_COLLECTION,
    ONBOARDING_CONFIGURE_MODEL_COMMAND,
    OnboardingService,
)

COMPONENT_SETUP_COLLECTION = "component-setup"


class OnboardingControlPlane(Protocol):
    def register_resource_service(self, collection: str, service: ResourceService) -> None: ...

    def register_command(self, command: str, handler: CommandHandler) -> None: ...


class OnboardingResourceService:
    """Expose one user-specific, non-mutating first-run status resource."""

    def __init__(
        self,
        onboarding: OnboardingService,
        *,
        component_setup: OnboardingComponentSetupService | None = None,
    ) -> None:
        self.onboarding = onboarding
        self.component_setup = component_setup

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        return (self._status(context),)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        if resource_id != FIRST_RUN_RESOURCE_ID:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"onboarding resource not found: {resource_id}",
            )
        return self._status(context)

    def _status(self, context: RequestContext) -> dict[str, JsonValue]:
        status = dict(self.onboarding.status(context))
        if self.component_setup is not None:
            status["component_setup"] = self.component_setup.status()
        return status


class ComponentSetupResourceService:
    """Expose provider-neutral discovery/profile state through the versioned Control Plane."""

    def __init__(self, component_setup: OnboardingComponentSetupService) -> None:
        self.component_setup = component_setup

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return (self.component_setup.status(),)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        if resource_id != COMPONENT_SETUP_RESOURCE_ID:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"component setup resource not found: {resource_id}",
            )
        return self.component_setup.status()


def register_component_setup_control_plane(
    control_plane: OnboardingControlPlane,
    component_setup: OnboardingComponentSetupService,
) -> None:
    """Register #799 discovery/profile surfaces without replacing owner-domain APIs."""

    control_plane.register_resource_service(
        COMPONENT_SETUP_COLLECTION,
        ComponentSetupResourceService(component_setup),
    )
    control_plane.register_command(
        ONBOARDING_SAVE_COMPONENT_PROFILE_COMMAND,
        component_setup.save_profile,
    )
    control_plane.register_command(
        ONBOARDING_SELECT_COMPONENT_PROFILE_COMMAND,
        component_setup.select_profile,
    )


def register_onboarding_control_plane(
    control_plane: OnboardingControlPlane,
    onboarding: OnboardingService,
    *,
    first_task: FirstRunTaskService | None = None,
    component_setup: OnboardingComponentSetupService | None = None,
) -> None:
    """Register orchestration surfaces while canonical owner domains remain authoritative.

    Project/Workspace, standard-Agent, model-provider, Executor and Task/Run lifecycle operations
    remain on their existing canonical APIs. Component setup only projects discovery/compatibility
    and persists reversible provider-neutral defaults.
    """

    control_plane.register_resource_service(
        ONBOARDING_COLLECTION,
        OnboardingResourceService(onboarding, component_setup=component_setup),
    )
    control_plane.register_command(
        ONBOARDING_CONFIGURE_MODEL_COMMAND,
        onboarding.configure_model,
    )
    if first_task is not None:
        control_plane.register_command(
            ONBOARDING_RUN_FIRST_TASK_COMMAND,
            first_task.run_first_task,
        )
    if component_setup is not None:
        control_plane.register_command(
            ONBOARDING_SAVE_COMPONENT_PROFILE_COMMAND,
            component_setup.save_profile,
        )
        control_plane.register_command(
            ONBOARDING_SELECT_COMPONENT_PROFILE_COMMAND,
            component_setup.select_profile,
        )
