from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
)
from ai_multi_agent_platform.conversations import ConversationService, JsonConversationRepository
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

CONVERSATION_RESOURCES = (
    "conversations",
    "conversation-messages",
    "conversation-exports",
)
CONVERSATION_COMMANDS = (
    "conversation.create",
    "conversation.archive",
    "conversation.reopen",
    "conversation.message.add",
    "conversation.message.create-task",
    "conversation.message.attach-task",
    "conversation.link-run",
    "conversation.link-artifact",
    "conversation.message.resume-task",
    "conversation.retention.set",
    "conversation.delete",
)
NOTIFICATION_RESOURCES = (
    "notifications",
    "notification-preferences",
)
NOTIFICATION_COMMANDS = (
    "notification.mark-read",
    "notification.mark-all-read",
    "notification.acknowledge",
    "notification.dismiss",
    "notification.archive",
    "notification.preference.update",
    "notification.delivery.retry",
)


def _control_plane(tmp_path: Path) -> ControlPlane:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    return ControlPlane(
        kernel=kernel,
        events=repository,
        conversation_service=ConversationService(
            JsonConversationRepository(tmp_path / "conversations.json")
        ),
    )


def test_conversation_resources_and_commands_have_one_explicit_owner(tmp_path: Path) -> None:
    control_plane = _control_plane(tmp_path)

    assert "conversations" in control_plane.registered_modules
    for resource in CONVERSATION_RESOURCES:
        assert control_plane.resource_owner(resource) == "conversations"
    for command in CONVERSATION_COMMANDS:
        assert control_plane.command_owner(command) == "conversations"


def test_notification_surface_has_one_explicit_owner(tmp_path: Path) -> None:
    control_plane = _control_plane(tmp_path)

    assert "notifications" in control_plane.registered_modules
    for resource in NOTIFICATION_RESOURCES:
        assert control_plane.resource_owner(resource) == "notifications"
    for command in NOTIFICATION_COMMANDS:
        assert control_plane.command_owner(command) == "notifications"
    assert control_plane.route_owner("GET", "/api/v1/notifications/stream") == "notifications"


def test_explicit_conversation_module_preserves_ergonomic_http_creation(tmp_path: Path) -> None:
    control_plane = _control_plane(tmp_path)
    http = ControlPlaneHTTP(control_plane)
    actor = ActorContext(
        principal_ref="user:alice",
        owner_type="user",
        owner_id="alice",
        actor_type="human",
    )

    response = asyncio.run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/conversations",
                headers={
                    "content-type": "application/json",
                    "idempotency-key": "conversation-explicit-module-create",
                },
                body={"title": "Explicit composition"},
                trusted_actor=actor,
            )
        )
    )

    assert response.status == 201, response.body
    assert response.body["type"] == "conversation"
    assert response.body["title"] == "Explicit composition"
