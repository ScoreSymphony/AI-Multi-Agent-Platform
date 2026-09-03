from __future__ import annotations

import asyncio

from ai_multi_agent_platform.control_plane import (
    ActorContext,
    AuthenticatedControlPlaneHTTP,
    ControlPlane,
    HTTPRequest,
    PageQuery,
    RequestContext,
)
from ai_multi_agent_platform.domain import Event, OwnerRef, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.notifications import (
    EventOwnerRecipientResolver,
    InMemoryNotificationPreferenceRepository,
    InMemoryNotificationRepository,
    NotificationCandidate,
    NotificationCategory,
    NotificationProjectingEventProvider,
    NotificationService,
    NotificationSeverity,
    RecipientRef,
    RecipientType,
    SourceRef,
    TaskTerminalNotificationRule,
)
from ai_multi_agent_platform.security import LocalAuthenticationService, ScryptPasswordHasher
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeEventProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)

PASSWORD = "correct horse battery staple"


def _control_plane() -> ControlPlane:
    repository = InMemoryKernelRepository()
    return ControlPlane(
        kernel=PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        ),
        events=repository,
        authorization=FakeAuthorizationProvider(allowed=True),
    )


def _context(user_id: str, *, key: str | None = None) -> RequestContext:
    return RequestContext(
        request_id="issue-75-request",
        correlation_id="issue-75-correlation",
        actor=ActorContext(
            principal_ref=user_id,
            owner_type="user",
            owner_id=user_id,
        ),
        idempotency_key=key,
    )


def _candidate(user_id: str) -> NotificationCandidate:
    task_id = new_id("task")
    return NotificationCandidate(
        category=NotificationCategory.TASK,
        severity=NotificationSeverity.ERROR,
        title="Task failed",
        summary={"status": "failed", "token": "must-not-leak"},
        recipient=RecipientRef(RecipientType.USER, user_id),
        source=SourceRef("task", task_id),
        task_id=task_id,
        aggregation_key=f"task:{task_id}:failed",
    )


def test_control_plane_inbox_is_recipient_scoped_and_commands_are_idempotent() -> None:
    async def scenario() -> None:
        control_plane = _control_plane()
        user_id = new_id("user")
        other_user_id = new_id("user")
        created = await control_plane.notification_service.create(_candidate(user_id))
        assert created is not None

        page = await control_plane.list_extension_resources(
            _context(user_id),
            "notifications",
            PageQuery(),
        )
        assert page["total"] == 1
        item = page["items"][0]
        assert item["id"] == created.id
        assert item["summary"]["token"] == "[REDACTED]"

        hidden = await control_plane.list_extension_resources(
            _context(other_user_id),
            "notifications",
            PageQuery(),
        )
        assert hidden["total"] == 0

        read = await control_plane.execute_command(
            _context(user_id, key="read-once"),
            "notification.mark-read",
            created.id,
        )
        reread = await control_plane.execute_command(
            _context(user_id, key="read-again"),
            "notification.mark-read",
            created.id,
        )
        assert read["state"] == "read"
        assert reread["state"] == "read"
        assert reread["read_at"] == read["read_at"]

        preferences = await control_plane.list_extension_resources(
            _context(user_id),
            "notification-preferences",
            PageQuery(),
        )
        assert preferences["items"][0]["unread_count"] == 0

    asyncio.run(scenario())


def test_control_plane_preference_update_cannot_target_another_recipient() -> None:
    async def scenario() -> None:
        control_plane = _control_plane()
        user_id = new_id("user")
        other_user_id = new_id("user")

        updated = await control_plane.execute_command(
            _context(user_id, key="preference-update"),
            "notification.preference.update",
            user_id,
            {
                "minimum_severity": "warning",
                "enabled_categories": ["task", "approval"],
                "muted": True,
            },
        )
        assert updated["minimum_severity"] == "warning"
        assert updated["muted"] is True

        try:
            await control_plane.execute_command(
                _context(user_id, key="preference-cross-user"),
                "notification.preference.update",
                other_user_id,
                {"muted": True},
            )
        except Exception as exc:
            assert getattr(exc, "code", None).value == "not_found"
        else:
            raise AssertionError("cross-recipient preference update must not succeed")

    asyncio.run(scenario())


def test_authenticated_http_ignores_spoofed_owner_headers_for_notification_inbox() -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        control_plane = ControlPlane(
            kernel=PlatformKernel(
                orchestrator=FakeOrchestrator(),
                lifecycle=FakeLifecycleBackend(),
                repository=repository,
            ),
            events=repository,
        )
        auth = LocalAuthenticationService(
            password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024)
        )
        user = auth.bootstrap_first_admin("issue-75-user", PASSWORD)
        token = auth.create_personal_access_token(user.user_id, purpose="issue-75-http")
        other_user_id = new_id("user")
        created = await control_plane.notification_service.create(_candidate(user.user_id))
        assert created is not None

        http = AuthenticatedControlPlaneHTTP(control_plane, auth, secure_cookie=False)
        response = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/notifications",
                headers={
                    "authorization": f"Bearer {token.secret}",
                    "x-principal-ref": other_user_id,
                    "x-owner-type": "user",
                    "x-owner-id": other_user_id,
                },
            )
        )

        assert response.status == 200
        assert response.body["total"] == 1
        assert response.body["items"][0]["id"] == created.id
        assert response.body["items"][0]["recipient"]["id"] == user.user_id

    asyncio.run(scenario())


def test_event_provider_projects_task_event_and_replay_aggregates_safely() -> None:
    async def scenario() -> None:
        repository = InMemoryNotificationRepository()
        preferences = InMemoryNotificationPreferenceRepository()
        service = NotificationService(
            repository=repository,
            preferences=preferences,
            rules=(TaskTerminalNotificationRule(EventOwnerRecipientResolver()),),
        )
        inner = FakeEventProvider()
        provider = NotificationProjectingEventProvider(inner, service)
        user_id = new_id("user")
        task_id = new_id("task")
        event = Event(
            event_type="task.failed",
            subject_type="task",
            subject_id=task_id,
            correlation_id=task_id,
            owner_ref=OwnerRef(type="user", id=user_id),
        )

        await provider.publish(event)
        recipient = RecipientRef(RecipientType.USER, user_id)
        inbox = await service.list(
            __import__(
                "ai_multi_agent_platform.notifications",
                fromlist=["NotificationQuery"],
            ).NotificationQuery(recipient=recipient)
        )
        assert len(inbox) == 1
        assert inbox[0].occurrence_count == 1

        projected = await provider.replay((event,))
        assert projected == 1
        replayed = await service.list(
            __import__(
                "ai_multi_agent_platform.notifications",
                fromlist=["NotificationQuery"],
            ).NotificationQuery(recipient=recipient)
        )
        assert len(replayed) == 1
        assert replayed[0].occurrence_count == 2

    asyncio.run(scenario())
