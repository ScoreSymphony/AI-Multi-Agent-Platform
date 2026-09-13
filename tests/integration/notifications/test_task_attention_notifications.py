from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.notifications import (
    NotificationCategory,
    NotificationQuery,
    RecipientRef,
    RecipientType,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _headers(user_id: str, key: str | None = None) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "x-principal-ref": user_id,
        "x-owner-type": "user",
        "x-owner-id": user_id,
    }
    if key is not None:
        headers["idempotency-key"] = key
    return headers


def _stack() -> tuple[ControlPlane, ControlPlaneHTTP]:
    repository = InMemoryKernelRepository()
    control_plane = ControlPlane(
        kernel=PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        ),
        events=repository,
        authorization=FakeAuthorizationProvider(allowed=True),
    )
    return control_plane, ControlPlaneHTTP(control_plane)


async def _create_task(
    http: ControlPlaneHTTP,
    user_id: str,
    *,
    key: str,
    title: str,
) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/tasks",
            headers=_headers(user_id, key),
            body={
                "title": title,
                "objective": f"Objective for {title}",
                "owner_type": "user",
                "owner_id": user_id,
            },
        )
    )
    assert response.status == 201, response.body
    assert isinstance(response.body, dict)
    return response.body


def test_task_management_changes_and_reminders_project_canonical_attention() -> None:
    async def scenario() -> None:
        control_plane, http = _stack()
        user_id = new_id("user")
        recipient = RecipientRef(RecipientType.USER, user_id)
        blocker = await _create_task(http, user_id, key="blocker", title="Blocker")
        target = await _create_task(http, user_id, key="target", title="Target")
        blocker_id = blocker["id"]
        target_id = target["id"]
        assert isinstance(blocker_id, str)
        assert isinstance(target_id, str)

        now = datetime.now(UTC)
        due_at = now + timedelta(hours=1)
        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/task-management.update",
                headers=_headers(user_id, "planning-update"),
                body={
                    "resource_ref": target_id,
                    "responsibility": {"kind": "user", "id": "alice"},
                    "dependencies": [{"task_id": blocker_id, "kind": "depends_on"}],
                    "due_at": due_at.isoformat(),
                },
            )
        )
        assert response.status == 200, response.body

        projected = await control_plane.notification_service.list(
            NotificationQuery(recipient=recipient)
        )
        categories = {item.category for item in projected}
        assert NotificationCategory.ASSIGNMENT in categories
        assert NotificationCategory.DEPENDENCY in categories
        assert NotificationCategory.DEADLINE in categories
        assert all(item.recipient == recipient for item in projected)
        dependency = next(
            item for item in projected if item.category is NotificationCategory.DEPENDENCY
        )
        assert dependency.summary["state"] == "blocked"
        assert dependency.summary["blocking_task_ids"] == [blocker_id]

        first_tick = await control_plane.evaluate_task_attention_reminders(now=now)
        second_tick = await control_plane.evaluate_task_attention_reminders(now=now)
        assert {item.id for item in first_tick} == {item.id for item in second_tick}

        after_ticks = await control_plane.notification_service.list(
            NotificationQuery(recipient=recipient)
        )
        approaching = [
            item
            for item in after_ticks
            if item.category is NotificationCategory.DEADLINE
            and item.summary.get("phase") == "approaching"
        ]
        assert len(approaching) == 1
        assert approaching[0].occurrence_count == 1
        blocked = [
            item
            for item in after_ticks
            if item.category is NotificationCategory.DEPENDENCY
            and item.summary.get("state") == "blocked"
        ]
        assert len(blocked) == 1
        assert blocked[0].occurrence_count == 1

    asyncio.run(scenario())


def test_overdue_reminder_uses_canonical_task_management_view() -> None:
    async def scenario() -> None:
        control_plane, http = _stack()
        user_id = new_id("user")
        recipient = RecipientRef(RecipientType.USER, user_id)
        target = await _create_task(http, user_id, key="overdue-target", title="Overdue target")
        target_id = target["id"]
        assert isinstance(target_id, str)
        now = datetime.now(UTC)
        due_at = now - timedelta(hours=1)

        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/task-management.update",
                headers=_headers(user_id, "overdue-update"),
                body={"resource_ref": target_id, "due_at": due_at.isoformat()},
            )
        )
        assert response.status == 200, response.body

        await control_plane.evaluate_task_attention_reminders(now=now)
        notifications = await control_plane.notification_service.list(
            NotificationQuery(recipient=recipient)
        )
        overdue = [
            item
            for item in notifications
            if item.category is NotificationCategory.DEADLINE
            and item.summary.get("phase") == "overdue"
        ]
        assert len(overdue) == 1
        assert overdue[0].severity.value == "error"
        assert overdue[0].source.resource_id == target_id

    asyncio.run(scenario())
