from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError
from ai_multi_agent_platform.control_plane import ControlPlane, RequestContext
from ai_multi_agent_platform.control_plane.models import ActorContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.organizations import (
    InMemoryOrganizationRepository,
    OrganizationService,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _context(principal_ref: str, key: str) -> RequestContext:
    return RequestContext(
        request_id=f"request-{key}",
        correlation_id=f"correlation-{key}",
        actor=ActorContext(principal_ref=principal_ref),
    )


def test_org_owner_and_admin_can_read_non_secret_ownership_metadata_without_membership() -> None:
    async def scenario() -> None:
        events = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=events,
        )
        repository = InMemoryOrganizationRepository()
        organizations = OrganizationService(repository)
        organization = await organizations.create_organization(
            name="Metadata Org",
            owner_actor_id="user:owner",
            administrator_actor_ids=("user:admin",),
        )
        team = await organizations.create_team(
            organization_id=organization.id,
            name="Readers",
        )
        ownership = await organizations.set_resource_owner(
            resource_type="template",
            resource_id="template_metadata",
            owner_ref=OwnerRef(type="organization", id=organization.id),
            organization_id=organization.id,
            created_by_actor_id="user:owner",
        )
        share = await organizations.share_resource(
            resource_type="template",
            resource_id="template_metadata",
            target_ref=OwnerRef(type="team", id=team.id),
            granted_by_actor_id="user:owner",
        )
        control_plane = ControlPlane(
            kernel=kernel,
            events=events,
            authorization=FakeAuthorizationProvider(),
            organization_service=organizations,
        )

        for principal in ("user:owner", "user:admin"):
            ownership_resource = await control_plane.get_extension_resource(
                _context(principal, f"{principal}-ownership"),
                "resource-ownerships",
                ownership.id,
            )
            share_resource = await control_plane.get_extension_resource(
                _context(principal, f"{principal}-share"),
                "resource-shares",
                share.id,
            )
            assert ownership_resource["id"] == ownership.id
            assert share_resource["id"] == share.id

        with pytest.raises(ContractError):
            await control_plane.get_extension_resource(
                _context("user:outsider", "outsider-ownership"),
                "resource-ownerships",
                ownership.id,
            )

    asyncio.run(scenario())
