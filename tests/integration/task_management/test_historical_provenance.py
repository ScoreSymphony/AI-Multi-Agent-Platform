from __future__ import annotations

import asyncio

from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.organizations import (
    InMemoryOrganizationRepository,
    OrganizationService,
)
from ai_multi_agent_platform.security.authorization import ActorType
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def test_historical_task_and_event_identity_survive_membership_removal() -> None:
    async def scenario() -> None:
        organization_repository = InMemoryOrganizationRepository()
        organizations = OrganizationService(organization_repository)
        organization = await organizations.create_organization(
            name="History Org",
            owner_actor_id="user:owner",
        )
        membership = await organizations.add_member(
            actor_id="user:former-member",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
        )

        events = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=events,
        )
        task = await kernel.create_task(
            idempotency_key="issue-87-historical-task",
            title="Historical work",
            objective="Preserve actor provenance after membership removal",
            owner_type="user",
            owner_id="user:former-member",
            actor_ref="user:former-member",
        )
        before_events = await events.read_events(task.task_id)
        assert len(before_events) == 1
        created_event = before_events[0]
        assert created_event.owner_ref == OwnerRef(type="user", id="user:former-member")
        assert created_event.provenance is not None
        assert created_event.provenance.actor_ref == "user:former-member"

        await organizations.remove_member(membership.id)

        historical = await kernel.get_task(task.task_id)
        historical_events = await events.read_events(task.task_id)
        assert historical.task.owner_ref == OwnerRef(type="user", id="user:former-member")
        assert historical.task_id == task.task_id
        assert historical_events == before_events
        assert historical_events[0].provenance is not None
        assert historical_events[0].provenance.actor_ref == "user:former-member"
        removed = await organizations.repository.get_membership(membership.id)
        assert removed.actor_id == "user:former-member"
        assert removed.status.value == "revoked"

    asyncio.run(scenario())
