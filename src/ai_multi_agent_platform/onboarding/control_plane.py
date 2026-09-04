"""Control Plane registration for the first-run onboarding composition."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import CommandHandler, ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .first_task import ONBOARDING_RUN_FIRST_TASK_COMMAND, FirstRunTaskService
from .service import (
    FIRST_RUN_RESOURCE_ID,
    ONBOARDING_COLLECTION,
    ONBOARDING_CONFIGURE_MODEL_COMMAND,
    OnboardingService,
)


class OnboardingControlPlane(Protocol):
    def register_resource_service(self, collection: str, service: ResourceService) -> None: ...

    def register_command(self, command: str, handler: CommandHandler) -> None: ...


class OnboardingResourceService:
    """Expose one user-specific, non-mutating first-run status resource."""

    def __init__(self, onboarding: OnboardingService) -> None:
        self.onboarding = onboarding

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        return (self.onboarding.status(context),)

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
        return self.onboarding.status(context)


def register_onboarding_control_plane(
    control_plane: OnboardingControlPlane,
    onboarding: OnboardingService,
    *,
    first_task: FirstRunTaskService | None = None,
) -> None:
    """Register only orchestration surfaces owned by issue #250.

    Project/Workspace, standard-Agent and Task/Run lifecycle operations remain on
    their existing canonical APIs and are merely referenced by onboarding status.
    """

    control_plane.register_resource_service(
        ONBOARDING_COLLECTION,
        OnboardingResourceService(onboarding),
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
