from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import ActorContext, ControlPlane, RequestContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.notifications import (
    NotificationCandidate,
    NotificationCategory,
    NotificationSeverity,
    RecipientRef,
    RecipientType,
    SourceRef,
)
from ai_multi_agent_platform.search import SearchMode, SearchQuery
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class SourceFilteringAuthorization(FakeAuthorizationProvider):
    def __init__(self) -> None:
        super().__init__()
        self.deny_task_reads = False

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if self.deny_task_reads and request.action == "task:read":
            return AuthorizationDecision(allowed=False, reason="source-hidden")
        return AuthorizationDecision(allowed=True, reason="visible")


def _control_plane(
    authorization: FakeAuthorizationProvider | None = None,
) -> ControlPlane:
    repository = InMemoryKernelRepository()
    return ControlPlane(
        kernel=PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        ),
        events=repository,
        authorization=authorization or FakeAuthorizationProvider(allowed=True),
    )


def _context(user_id: str) -> RequestContext:
    return RequestContext(
        request_id="issue-75-search-request",
        correlation_id="issue-75-search-correlation",
        actor=ActorContext(
            principal_ref=user_id,
            owner_type="user",
            owner_id=user_id,
        ),
    )


def _candidate(user_id: str) -> NotificationCandidate:
    task_id = new_id("task")
    return NotificationCandidate(
        category=NotificationCategory.TASK,
        severity=NotificationSeverity.ERROR,
        title="Private task title must not become Search text",
        summary={
            "status": "failed",
            "private_detail": "search-summary-secret",
        },
        recipient=RecipientRef(RecipientType.USER, user_id),
        source=SourceRef("task", task_id),
        task_id=task_id,
        aggregation_key=f"task:{task_id}:failed",
        delivery_metadata={
            "provider": "private-provider",
            "channel": "private-channel",
            "token": "search-delivery-secret",
        },
    )


def test_notification_search_is_recipient_scoped_minimized_and_discoverable() -> None:
    async def scenario() -> None:
        control_plane = _control_plane()
        user_id = new_id("user")
        other_user_id = new_id("user")
        created = await control_plane.notification_service.create(_candidate(user_id))
        assert created is not None

        exact = await control_plane.search_resources(
            _context(user_id),
            SearchQuery(
                exact_id=created.id,
                resource_types=("notification",),
                mode=SearchMode.EXACT,
            ),
        )
        assert exact["total"] == 1
        item = exact["items"][0]
        assert item["resource_type"] == "notification"
        assert item["resource_id"] == created.id
        assert item["status"] == "unread"
        assert item["summary"] == ""
        assert item["owner_type"] == "user"
        assert item["owner_id"] == user_id
        assert item["canonical_ref"] == f"/api/v1/notifications/{created.id}"
        assert item["provenance"] == {"indexed_from": "canonical-notification-repository"}

        serialized = repr(exact)
        assert created.title not in serialized
        assert "search-summary-secret" not in serialized
        assert "private-provider" not in serialized
        assert "private-channel" not in serialized
        assert "search-delivery-secret" not in serialized

        source_lookup = await control_plane.search_resources(
            _context(user_id),
            SearchQuery(text=created.source.resource_id, resource_types=("notification",)),
        )
        assert source_lookup["total"] == 1
        metadata_lookup = await control_plane.search_resources(
            _context(user_id),
            SearchQuery(
                resource_types=("notification",),
                statuses=("unread",),
                tags=("task", "error"),
                mode=SearchMode.METADATA,
            ),
        )
        assert metadata_lookup["total"] == 1

        hidden = await control_plane.search_resources(
            _context(other_user_id),
            SearchQuery(
                exact_id=created.id,
                resource_types=("notification",),
                mode=SearchMode.EXACT,
            ),
        )
        assert hidden["total"] == 0
        assert hidden["items"] == []
        assert created.id not in repr(hidden)

    asyncio.run(scenario())


def test_notification_search_rechecks_source_visibility_and_retention_state() -> None:
    async def scenario() -> None:
        authorization = SourceFilteringAuthorization()
        control_plane = _control_plane(authorization)
        user_id = new_id("user")
        recipient = RecipientRef(RecipientType.USER, user_id)
        created = await control_plane.notification_service.create(_candidate(user_id))
        assert created is not None
        assert await control_plane.rebuild_search_index() >= 1

        authorization.deny_task_reads = True
        hidden = await control_plane.search_resources(
            _context(user_id),
            SearchQuery(
                exact_id=created.id,
                resource_types=("notification",),
                mode=SearchMode.EXACT,
            ),
        )
        assert hidden["total"] == 0
        assert hidden["items"] == []

        authorization.deny_task_reads = False
        await control_plane.notification_service.mark_read(created.id, recipient=recipient)
        read = await control_plane.search_resources(
            _context(user_id),
            SearchQuery(
                resource_types=("notification",),
                statuses=("read",),
                mode=SearchMode.METADATA,
            ),
        )
        assert read["total"] == 1
        assert read["items"][0]["resource_id"] == created.id

        await control_plane.notification_service.archive(created.id, recipient=recipient)
        archived = await control_plane.search_resources(
            _context(user_id),
            SearchQuery(
                exact_id=created.id,
                resource_types=("notification",),
                mode=SearchMode.EXACT,
            ),
        )
        assert archived["total"] == 0
        assert archived["items"] == []

        current = datetime.now(UTC)
        expired_candidate = replace(
            _candidate(user_id),
            aggregation_key=None,
            expires_at=current - timedelta(hours=1),
        )
        expired = await control_plane.notification_service.create(
            expired_candidate,
            now=current - timedelta(days=1),
        )
        assert expired is not None
        expired_search = await control_plane.search_resources(
            _context(user_id),
            SearchQuery(
                exact_id=expired.id,
                resource_types=("notification",),
                mode=SearchMode.EXACT,
            ),
        )
        assert expired_search["total"] == 0
        assert expired_search["items"] == []

    asyncio.run(scenario())
