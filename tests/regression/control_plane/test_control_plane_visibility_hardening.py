from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import AuthorizationDecision, ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    PageQuery,
    RequestContext,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.notifications import (
    NotificationCandidate,
    NotificationCategory,
    NotificationQuery,
    NotificationSeverity,
    NotificationState,
    RecipientRef,
    RecipientType,
    SourceRef,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class _SourceVisibilityAuthorization(FakeAuthorizationProvider):
    def __init__(self) -> None:
        super().__init__()
        self.source_visible = True

    async def authorize(self, request):
        self.calls.append(request)
        denied = request.action == "task:read" and not self.source_visible
        return AuthorizationDecision(allowed=not denied, reason="source visibility fixture")


def _control_plane(authorization: _SourceVisibilityAuthorization) -> ControlPlane:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    return ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=authorization,
    )


def _context(team_id: str, *, key: str | None = None) -> RequestContext:
    return RequestContext(
        request_id="issue75-source-visibility",
        correlation_id="issue75-source-visibility",
        idempotency_key=key,
        actor=ActorContext(
            principal_ref="user:operator",
            actor_type="human",
            owner_type="team",
            owner_id=team_id,
        ),
    )


def _candidate(recipient: RecipientRef, task_id: str, project_id: str):
    return NotificationCandidate(
        category=NotificationCategory.TASK,
        severity=NotificationSeverity.WARNING,
        title="Task attention",
        summary={"status": "failed"},
        recipient=recipient,
        source=SourceRef("task", task_id),
        task_id=task_id,
        project_id=project_id,
        aggregation_key=f"task:{task_id}:failed",
    )


def test_control_plane_hides_historical_attention_after_source_authorization_revocation() -> None:
    async def scenario() -> None:
        authorization = _SourceVisibilityAuthorization()
        control_plane = _control_plane(authorization)
        team_id = new_id("team")
        recipient = RecipientRef(RecipientType.TEAM, team_id)
        task_id = new_id("task")
        project_id = new_id("project")
        created = await control_plane.notification_service.create(
            _candidate(recipient, task_id, project_id)
        )
        assert created is not None

        authorization.source_visible = False
        context = _context(team_id)
        page = await control_plane.list_extension_resources(
            context,
            "notifications",
            PageQuery(),
        )
        preferences = await control_plane.list_extension_resources(
            context,
            "notification-preferences",
            PageQuery(),
        )

        assert page["items"] == []
        assert page["total"] == 0
        assert preferences["items"][0]["unread_count"] == 0
        with pytest.raises(ContractError) as exc_info:
            await control_plane.get_extension_resource(context, "notifications", created.id)
        assert exc_info.value.code is ErrorCode.NOT_FOUND

        source_requests = [call for call in authorization.calls if call.action == "task:read"]
        assert source_requests
        assert all(call.principal_ref == "user:operator" for call in source_requests)
        assert all(call.context.owner_type == "team" for call in source_requests)
        assert all(call.context.owner_id == team_id for call in source_requests)
        assert all(call.context.project_id == project_id for call in source_requests)

    asyncio.run(scenario())


def test_mark_all_read_mutates_only_currently_authorized_attention() -> None:
    async def scenario() -> None:
        authorization = _SourceVisibilityAuthorization()
        control_plane = _control_plane(authorization)
        team_id = new_id("team")
        recipient = RecipientRef(RecipientType.TEAM, team_id)
        task_id = new_id("task")
        created = await control_plane.notification_service.create(
            _candidate(recipient, task_id, new_id("project"))
        )
        assert created is not None

        authorization.source_visible = False
        hidden_result = await control_plane.execute_command(
            _context(team_id, key="hidden-mark-all"),
            "notification.mark-all-read",
            "notifications",
            {},
        )
        hidden = await control_plane.notification_service.list(
            NotificationQuery(recipient=recipient, unread_only=True)
        )

        assert hidden_result["updated_count"] == 0
        assert hidden_result["unread_count"] == 0
        assert [item.id for item in hidden] == [created.id]
        assert hidden[0].state is NotificationState.UNREAD

        authorization.source_visible = True
        visible_result = await control_plane.execute_command(
            _context(team_id, key="visible-mark-all"),
            "notification.mark-all-read",
            "notifications",
            {},
        )
        reread = await control_plane.notification_service.get(created.id, recipient=recipient)

        assert visible_result["updated_count"] == 1
        assert visible_result["unread_count"] == 0
        assert reread.state is NotificationState.READ

    asyncio.run(scenario())


def test_notification_actions_return_not_found_when_source_access_is_revoked() -> None:
    async def scenario() -> None:
        authorization = _SourceVisibilityAuthorization()
        control_plane = _control_plane(authorization)
        team_id = new_id("team")
        recipient = RecipientRef(RecipientType.TEAM, team_id)
        created = await control_plane.notification_service.create(
            _candidate(recipient, new_id("task"), new_id("project"))
        )
        assert created is not None

        authorization.source_visible = False
        with pytest.raises(ContractError) as exc_info:
            await control_plane.execute_command(
                _context(team_id, key="hidden-acknowledge"),
                "notification.acknowledge",
                created.id,
                {},
            )

        assert exc_info.value.code is ErrorCode.NOT_FOUND
        persisted = await control_plane.notification_service.get(created.id, recipient=recipient)
        assert persisted.state is NotificationState.UNREAD

    asyncio.run(scenario())
