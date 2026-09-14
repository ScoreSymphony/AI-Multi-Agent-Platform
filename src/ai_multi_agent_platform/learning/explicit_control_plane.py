"""Explicit module-owned Learning composition for the canonical single-node runtime (#982)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from ai_multi_agent_platform.control_plane.extensions import (
    CommandHandler,
    ControlPlane,
    ControlPlaneModule,
    ResourceService,
)
from ai_multi_agent_platform.control_plane.module_registry import install_control_plane_modules

from .control_plane import (
    LEARNING_CANDIDATE_COLLECTION,
    LEARNING_COMMANDS,
    LEARNING_FEEDBACK_COLLECTION,
    register_learning_control_plane,
)
from .runtime import ObservedLearningService
from .runtime_control_plane import (
    LEARNING_POST_PROMOTION_COLLECTION,
    register_learning_runtime_control_plane,
)
from .scoped_control_plane import (
    ScopedLearningCommand,
    ScopedLearningFeedbackResourceService,
    ScopedLearningPostPromotionResourceService,
    ScopedRuntimeAwareLearningCandidateResourceService,
    _install_deferred_authorization,
)

LEARNING_MODULE = "learning"


@dataclass(slots=True)
class _LearningRegistrationCollector:
    """Materialize legacy Learning layers without mutating the real Control Plane."""

    resource_services: dict[str, ResourceService] = field(default_factory=dict)
    command_handlers: dict[str, CommandHandler] = field(default_factory=dict)

    def register_resource_service(self, collection: str, service: ResourceService) -> None:
        self.resource_services[collection] = service

    def register_command(self, command: str, handler: CommandHandler) -> None:
        self.command_handlers[command] = handler


def register_explicit_scoped_learning_control_plane(
    control_plane: ControlPlane,
    learning: ObservedLearningService,
) -> None:
    """Install the final scoped/runtime-aware Learning surface exactly once."""

    access = _install_deferred_authorization(control_plane)
    collector = _LearningRegistrationCollector()
    registration_target = cast(ControlPlane, cast(object, collector))

    register_learning_control_plane(registration_target, learning)
    register_learning_runtime_control_plane(registration_target, learning)

    collector.resource_services[LEARNING_CANDIDATE_COLLECTION] = (
        ScopedRuntimeAwareLearningCandidateResourceService(learning, access)
    )
    collector.resource_services[LEARNING_FEEDBACK_COLLECTION] = (
        ScopedLearningFeedbackResourceService(learning, access)
    )
    collector.resource_services[LEARNING_POST_PROMOTION_COLLECTION] = (
        ScopedLearningPostPromotionResourceService(learning, access)
    )

    scoped_handlers: dict[str, CommandHandler] = {}
    for action in LEARNING_COMMANDS:
        delegate = collector.command_handlers[action]
        scoped_handlers[action] = ScopedLearningCommand(action, delegate, learning, access)

    install_control_plane_modules(
        control_plane,
        (
            ControlPlaneModule(
                name=LEARNING_MODULE,
                resource_services=dict(collector.resource_services),
                command_handlers=scoped_handlers,
            ),
        ),
    )


__all__ = ["LEARNING_MODULE", "register_explicit_scoped_learning_control_plane"]
