from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError
from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
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


class CrossOrganizationAuthorization(FakeAuthorizationProvider):
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        return AuthorizationDecision(
            allowed=request.action != "resource-share.cross-organization",
            reason="cross-organization-policy",
        )


def test_cross_org_share_flag_is_not_a_substitute_for_authorization() -> None:
    async def scenario() -> None:
        events = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=events,
        )
        repository = InMemoryOrganizationRepository()
        organizations = OrganizationService(repository)
        source = await organizations.create_organization(
            name="Source",
            owner_actor_id="user:source-owner",
        )
        target = await organizations.create_organization(
            name="Target",
            owner_actor_id="user:target-owner",
        )
        await organizations.set_resource_owner(
            resource_type="template",
            resource_id="template_cross-org",
            owner_ref=OwnerRef(type="organization", id=source.id),
            organization_id=source.id,
            created_by_actor_id="user:source-owner",
        )
        authorization = CrossOrganizationAuthorization()
        control_plane = ControlPlane(
            kernel=kernel,
            events=events,
            authorization=authorization,
            organization_service=organizations,
        )
        context = RequestContext(
            request_id="request-cross-org",
            correlation_id="correlation-cross-org",
            actor=ActorContext(
                principal_ref="user:source-owner",
                owner_type="organization",
                owner_id=source.id,
            ),
            idempotency_key="cross-org-share",
        )

        with pytest.raises(ContractError):
            await control_plane.execute_command(
                context,
                "resource-share.create",
                "template_cross-org",
                {
                    "resource_type": "template",
                    "resource_id": "template_cross-org",
                    "target_ref": {"type": "organization", "id": target.id},
                    "allow_cross_organization": True,
                },
            )

        assert await repository.list_all_shares() == ()
        assert any(
            call.action == "resource-share.cross-organization" for call in authorization.calls
        )

    asyncio.run(scenario())
