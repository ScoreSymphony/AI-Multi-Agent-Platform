from __future__ import annotations

import asyncio

from ai_multi_agent_platform.accounting import (
    AccountingService,
    InMemoryUsageStore,
    MeasurementQuality,
    UsageRecord,
    UsageScope,
)
from ai_multi_agent_platform.control_plane import ControlPlane
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.organizations.accounting import (
    DEFAULT_ACCOUNTING_AGGREGATE_POLICY_REF,
)
from ai_multi_agent_platform.organizations.models import Membership, Organization
from ai_multi_agent_platform.organizations.repository import InMemoryOrganizationRepository
from ai_multi_agent_platform.organizations.service import OrganizationService
from ai_multi_agent_platform.security.authorization import ActorType
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


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


def test_current_control_plane_composes_exact_owner_accounting_without_organizations() -> None:
    async def scenario() -> None:
        accounting = AccountingService(InMemoryUsageStore())
        accounting.record(
            UsageRecord(
                metric_type="task.count",
                unit="count",
                quality=MeasurementQuality.MEASURED,
                source="test",
                quantity=2.0,
                scope=UsageScope(owner_type="user", owner_id="alice"),
            )
        )
        accounting.record(
            UsageRecord(
                metric_type="task.count",
                unit="count",
                quality=MeasurementQuality.MEASURED,
                source="test",
                quantity=100.0,
                scope=UsageScope(owner_type="user", owner_id="bob"),
            )
        )
        kernel, events = _kernel()
        control_plane = ControlPlane(
            kernel=kernel,
            events=events,
            accounting_service=accounting,
        )

        assert {"usage-records", "usage-aggregates", "usage-budgets"}.issubset(
            control_plane.registered_collections
        )
        result = await control_plane.list_extension_resources(
            _context("alice"),
            "usage-aggregates",
            PageQuery(),
        )
        items = result["items"]
        assert isinstance(items, list)
        assert len(items) == 1
        assert items[0]["total"] == 2.0

    asyncio.run(scenario())


def test_current_control_plane_upgrades_usage_visibility_when_organizations_are_configured() -> (
    None
):
    async def scenario() -> None:
        organization_repository = InMemoryOrganizationRepository()
        organizations = OrganizationService(organization_repository)
        organization = await organization_repository.save_organization(
            Organization(name="Example", owner_actor_id="owner")
        )
        await organization_repository.save_membership(
            Membership(
                actor_id="alice",
                actor_type=ActorType.HUMAN,
                organization_id=organization.id,
                policy_refs=(DEFAULT_ACCOUNTING_AGGREGATE_POLICY_REF,),
            )
        )
        accounting = AccountingService(InMemoryUsageStore())
        for owner_id, quantity in (("bob", 2.0), ("carol", 3.0)):
            accounting.record(
                UsageRecord(
                    metric_type="task.count",
                    unit="count",
                    quality=MeasurementQuality.MEASURED,
                    source="test",
                    quantity=quantity,
                    scope=UsageScope(
                        organization_id=organization.id,
                        owner_type="user",
                        owner_id=owner_id,
                    ),
                )
            )

        kernel, events = _kernel()
        control_plane = ControlPlane(
            kernel=kernel,
            events=events,
            organization_service=organizations,
            accounting_service=accounting,
        )
        result = await control_plane.list_extension_resources(
            _context("alice"),
            "usage-aggregates",
            PageQuery(),
        )
        items = result["items"]
        assert isinstance(items, list)
        organization_items = [
            item
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("scope"), dict)
            and item["scope"].get("organization_id") == organization.id
        ]
        assert len(organization_items) == 1
        assert organization_items[0]["total"] == 5.0

    asyncio.run(scenario())
