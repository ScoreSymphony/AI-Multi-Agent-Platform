from __future__ import annotations

import asyncio

from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.notifications import (
    InMemoryNotificationPreferenceRepository,
    InMemoryNotificationRepository,
    NotificationCategory,
    NotificationQuery,
    NotificationService,
    RecipientRef,
    RecipientType,
    SourceRef,
    canonical_attention_candidate,
    membership_attention_candidate,
)


def _recipient(kind: RecipientType = RecipientType.USER) -> RecipientRef:
    return RecipientRef(kind, new_id(kind.value))


def test_operational_attention_domains_have_provider_neutral_projection_hook() -> None:
    recipient = _recipient()
    node_id = new_id("node")
    automation_id = new_id("automation")
    cases = (
        (
            NotificationCategory.AGENT_INPUT,
            SourceRef("task", new_id("task")),
            "input_required",
            {},
        ),
        (
            NotificationCategory.WORKER,
            SourceRef("node", node_id),
            "unhealthy",
            {"node_id": node_id},
        ),
        (
            NotificationCategory.AUTOMATION,
            SourceRef("automation", automation_id),
            "failed",
            {"automation_id": automation_id},
        ),
        (
            NotificationCategory.SECURITY,
            SourceRef("event", new_id("event")),
            "operator_attention_required",
            {},
        ),
        (
            NotificationCategory.CONNECTOR,
            SourceRef("connector", new_id("connector")),
            "delivery_failed",
            {},
        ),
    )

    for category, source, attention, references in cases:
        candidate = canonical_attention_candidate(
            category=category,
            recipient=recipient,
            source=source,
            attention=attention,
            title=f"{category.value} attention",
            **references,
        )
        duplicate = canonical_attention_candidate(
            category=category,
            recipient=recipient,
            source=source,
            attention=attention,
            title=f"{category.value} attention",
            **references,
        )

        assert candidate.category is category
        assert candidate.source == source
        assert candidate.resource_ref == source
        assert candidate.summary == {"attention": attention}
        assert candidate.aggregation_key == duplicate.aggregation_key


def test_organization_and_project_notification_isolation_is_recipient_scoped() -> None:
    async def scenario() -> None:
        organization = _recipient(RecipientType.ORGANIZATION)
        other_organization = _recipient(RecipientType.ORGANIZATION)
        project_id = new_id("project")
        other_project_id = new_id("project")
        membership_id = new_id("membership")
        service = NotificationService(
            repository=InMemoryNotificationRepository(),
            preferences=InMemoryNotificationPreferenceRepository(),
        )

        created = await service.create(
            membership_attention_candidate(
                membership_id=membership_id,
                recipient=organization,
                attention="invitation_pending",
                title="Organization invitation",
                project_id=project_id,
            )
        )
        assert created is not None

        wrong_organization = await service.list(
            NotificationQuery(recipient=other_organization, project_id=project_id)
        )
        wrong_project = await service.list(
            NotificationQuery(recipient=organization, project_id=other_project_id)
        )
        visible = await service.list(
            NotificationQuery(recipient=organization, project_id=project_id)
        )

        assert wrong_organization == ()
        assert wrong_project == ()
        assert tuple(item.id for item in visible) == (created.id,)
        assert await service.unread_count(other_organization) == 0
        assert await service.unread_count(organization) == 1

    asyncio.run(scenario())
