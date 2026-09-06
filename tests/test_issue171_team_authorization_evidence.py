from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.accounting import (
    AccountingService,
    InMemoryUsageStore,
    MeasurementQuality,
    UsageRecord,
    UsageScope,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ControlPlane
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.organizations.accounting import DEFAULT_ACCOUNTING_AGGREGATE_POLICY_REF
from ai_multi_agent_platform.organizations.models import Membership, Organization, Team
from ai_multi_agent_platform.organizations.repository import InMemoryOrganizationRepository
from ai_multi_agent_platform.organizations.service import OrganizationService
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    ControlPlaneAuthorizationBridge,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _kernel() -> tuple[PlatformKernel, InMemoryKernelRepository]:
    repository = InMemoryKernelRepository()
    return (
        PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        ),
        repository,
    )


def _context(actor_id: str) -> RequestContext:
    return RequestContext(
        request_id=f"request-{actor_id}",
        correlation_id=f"correlation-{actor_id}",
        actor=ActorContext(
            principal_ref=actor_id,
            owner_type="user",
            owner_id=actor_id,
            actor_type="human",
        ),
    )


def test_team_aggregate_requires_both_authorization_and_membership() -> None:
    async def scenario() -> None:
        repository = InMemoryOrganizationRepository()
        organizations = OrganizationService(repository)
        organization = await repository.save_organization(
            Organization(name="Accounting organization", owner_actor_id="owner")
        )
        team = await repository.save_team(
            Team(organization_id=organization.id, name="Accounting team")
        )

        for actor_id in ("alice", "mallory"):
            await repository.save_membership(
                Membership(
                    actor_id=actor_id,
                    actor_type=ActorType.HUMAN,
                    organization_id=organization.id,
                    team_id=team.id,
                    policy_refs=(DEFAULT_ACCOUNTING_AGGREGATE_POLICY_REF,),
                )
            )

        accounting = AccountingService(InMemoryUsageStore())
        accounting.record(
            UsageRecord(
                metric_type="task.count",
                unit="count",
                quality=MeasurementQuality.MEASURED,
                source="test",
                quantity=3.0,
                scope=UsageScope(
                    organization_id=organization.id,
                    team_id=team.id,
                    owner_type="user",
                    owner_id="worker-owner",
                ),
            )
        )

        authorization = LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="alice",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.VIEW}),
                    resource_types=frozenset({ResourceType.GENERIC}),
                ),
                LocalPrincipalPolicy(
                    principal_ref="outsider",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.VIEW}),
                    resource_types=frozenset({ResourceType.GENERIC}),
                ),
                LocalPrincipalPolicy(
                    principal_ref="mallory",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.READ}),
                    resource_types=frozenset({ResourceType.GENERIC}),
                ),
            )
        )
        kernel, events = _kernel()
        control_plane = ControlPlane(
            kernel=kernel,
            events=events,
            authorization=ControlPlaneAuthorizationBridge(AuthorizationGate(authorization)),
            organization_service=organizations,
            accounting_service=accounting,
        )

        allowed = await control_plane.list_extension_resources(
            _context("alice"), "usage-aggregates", PageQuery()
        )
        allowed_items = allowed["items"]
        assert isinstance(allowed_items, list)
        team_items = [
            item
            for item in allowed_items
            if isinstance(item, dict)
            and isinstance(item.get("scope"), dict)
            and item["scope"].get("team_id") == team.id
        ]
        assert len(team_items) == 1
        assert team_items[0]["total"] == 3.0
        assert team_items[0]["scope"].get("owner_type") == "team"
        assert team_items[0]["scope"].get("owner_id") == team.id
        assert not any(
            isinstance(item, dict)
            and isinstance(item.get("scope"), dict)
            and item["scope"].get("owner_type") == "organization"
            for item in allowed_items
        )

        # #15 allows the request, but absent #87 Team membership still yields no Team data.
        outsider = await control_plane.list_extension_resources(
            _context("outsider"), "usage-aggregates", PageQuery()
        )
        outsider_items = outsider["items"]
        assert isinstance(outsider_items, list)
        assert not any(
            isinstance(item, dict)
            and isinstance(item.get("scope"), dict)
            and item["scope"].get("team_id") == team.id
            for item in outsider_items
        )

        # #87 grants Mallory the same Team aggregate policy as Alice, but #15 lacks VIEW.
        # Membership must never widen a denied Control Plane accounting request.
        with pytest.raises(ContractError) as denied:
            await control_plane.list_extension_resources(
                _context("mallory"), "usage-aggregates", PageQuery()
            )
        assert denied.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())
