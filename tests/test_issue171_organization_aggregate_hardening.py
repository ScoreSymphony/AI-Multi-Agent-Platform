from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

from ai_multi_agent_platform.accounting import (
    AccountingService,
    InMemoryUsageStore,
    MeasurementQuality,
    UsageRecord,
    UsageScope,
)
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.organizations.accounting import (
    DEFAULT_ACCOUNTING_AGGREGATE_POLICY_REF,
    organization_accounting_resource_services,
)
from ai_multi_agent_platform.organizations.models import Membership, MembershipStatus, Organization
from ai_multi_agent_platform.organizations.repository import InMemoryOrganizationRepository
from ai_multi_agent_platform.organizations.service import OrganizationService
from ai_multi_agent_platform.security.authorization import ActorType


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


def test_real_organization_membership_grants_only_aggregate_visibility() -> None:
    async def scenario() -> None:
        repository = InMemoryOrganizationRepository()
        organizations = OrganizationService(repository)
        organization = await repository.save_organization(
            Organization(name="Example", owner_actor_id="owner")
        )
        other_organization = await repository.save_organization(
            Organization(name="Other", owner_actor_id="other-owner")
        )
        membership = await repository.save_membership(
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
                        task_id=new_id("task"),
                        run_id=new_id("run"),
                        agent_id=new_id("agent"),
                        owner_type="user",
                        owner_id=owner_id,
                    ),
                )
            )
        accounting.record(
            UsageRecord(
                metric_type="task.count",
                unit="count",
                quality=MeasurementQuality.MEASURED,
                source="test",
                quantity=7.0,
                scope=UsageScope(
                    organization_id=organization.id,
                    task_id=new_id("task"),
                    owner_type="organization",
                    owner_id=organization.id,
                ),
            )
        )
        accounting.record(
            UsageRecord(
                metric_type="task.count",
                unit="count",
                quality=MeasurementQuality.MEASURED,
                source="test",
                quantity=100.0,
                scope=UsageScope(
                    organization_id=other_organization.id,
                    owner_type="user",
                    owner_id="mallory",
                ),
            )
        )

        services = organization_accounting_resource_services(accounting, organizations)
        context = _context("alice")
        aggregates = await services["usage-aggregates"].list_resources(context, PageQuery())
        organization_aggregates = [
            item
            for item in aggregates
            if isinstance(item.get("scope"), dict)
            and item["scope"].get("organization_id") == organization.id
            and item["scope"].get("owner_type") == "organization"
        ]
        assert len(organization_aggregates) == 1
        assert organization_aggregates[0]["total"] == 12.0
        assert organization_aggregates[0]["scope"].get("task_id") is None
        assert organization_aggregates[0]["scope"].get("run_id") is None
        assert organization_aggregates[0]["scope"].get("agent_id") is None
        assert not any(
            isinstance(item.get("scope"), dict)
            and item["scope"].get("organization_id") == other_organization.id
            for item in aggregates
        )

        raw = await services["usage-records"].list_resources(context, PageQuery())
        assert raw == ()

        await repository.save_membership(
            replace(
                membership,
                status=MembershipStatus.SUSPENDED,
                suspended_at=datetime(2026, 9, 5, 21, 0, tzinfo=UTC),
            )
        )
        after = await services["usage-aggregates"].list_resources(context, PageQuery())
        assert not any(
            isinstance(item.get("scope"), dict)
            and item["scope"].get("organization_id") == organization.id
            and item["scope"].get("owner_type") == "organization"
            for item in after
        )

    asyncio.run(scenario())
