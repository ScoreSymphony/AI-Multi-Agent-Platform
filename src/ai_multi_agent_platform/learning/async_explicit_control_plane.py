"""Explicit module-owned async Learning composition for the single-node runtime."""

from __future__ import annotations

from ai_multi_agent_platform.control_plane.extensions import (
    CommandHandler,
    ControlPlane,
    ControlPlaneModule,
    ResourceService,
)
from ai_multi_agent_platform.control_plane.module_registry import install_control_plane_modules

from .async_control_plane import (
    AsyncLearningCandidateResourceService,
    AsyncLearningFeedbackResourceService,
    AsyncLearningPostPromotionResourceService,
    AsyncScopedLearningCommand,
    _handlers,
)
from .async_service import RuntimeGovernedLearningService
from .control_plane import LEARNING_CANDIDATE_COLLECTION, LEARNING_FEEDBACK_COLLECTION
from .explicit_control_plane import LEARNING_MODULE
from .runtime_adapter import LearningRuntimeAdapter
from .runtime_control_plane import LEARNING_POST_PROMOTION_COLLECTION
from .scoped_control_plane import _install_deferred_authorization


def register_async_explicit_scoped_learning_control_plane(
    control_plane: ControlPlane,
    learning: RuntimeGovernedLearningService,
) -> None:
    """Install awaitable Learning surfaces through the explicit module registry."""

    runtime = LearningRuntimeAdapter(learning)
    access = _install_deferred_authorization(control_plane)
    handlers = _handlers(runtime)
    resource_services: dict[str, ResourceService] = {
        LEARNING_CANDIDATE_COLLECTION: AsyncLearningCandidateResourceService(runtime, access),
        LEARNING_FEEDBACK_COLLECTION: AsyncLearningFeedbackResourceService(runtime, access),
        LEARNING_POST_PROMOTION_COLLECTION: AsyncLearningPostPromotionResourceService(
            runtime, access
        ),
    }
    command_handlers: dict[str, CommandHandler] = {
        action: AsyncScopedLearningCommand(action, handler, runtime, access)
        for action, handler in handlers.items()
    }

    install_control_plane_modules(
        control_plane,
        (
            ControlPlaneModule(
                name=LEARNING_MODULE,
                resource_services=resource_services,
                command_handlers=command_handlers,
            ),
        ),
    )


__all__ = ["register_async_explicit_scoped_learning_control_plane"]
