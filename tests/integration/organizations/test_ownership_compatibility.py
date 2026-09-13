from __future__ import annotations

import asyncio
from typing import cast

import pytest

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.domain import OwnerRef, Project
from ai_multi_agent_platform.kernel import TaskState
from ai_multi_agent_platform.organizations import (
    InMemoryOrganizationRepository,
    OrganizationService,
)
from ai_multi_agent_platform.security.authorization import ActorType
from ai_multi_agent_platform.task_reassignment import DefaultTaskProjectCompatibilityPolicy


def test_membership_allows_personal_to_organization_but_not_reverse_ownership() -> None:
    async def scenario() -> None:
        organizations = OrganizationService(InMemoryOrganizationRepository())
        organization = await organizations.create_organization(
            name="Example",
            owner_actor_id="user:owner",
        )
        await organizations.add_member(
            actor_id="user:alice",
            actor_type=ActorType.HUMAN,
            organization_id=organization.id,
        )
        personal = Project(
            name="Personal",
            owner_ref=OwnerRef(type="user", id="user:alice"),
        )
        organizational = Project(
            name="Organization",
            owner_ref=OwnerRef(type="organization", id=organization.id),
        )
        policy = DefaultTaskProjectCompatibilityPolicy(organizations)
        unused_task = cast(TaskState, object())

        await policy.require_compatible(
            task=unused_task,
            source_project=personal,
            destination_project=organizational,
        )

        with pytest.raises(ContractError, match="ownership scopes are incompatible"):
            await policy.require_compatible(
                task=unused_task,
                source_project=organizational,
                destination_project=personal,
            )

    asyncio.run(scenario())
