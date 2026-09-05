"""#87 membership-aware visibility for canonical #76 accounting resources."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.accounting.control_plane import (
    _aggregate_resources,
    _budget_resource,
    _record_resource,
)
from ai_multi_agent_platform.accounting.models import (
    UsageBudget,
    UsageQuery,
    UsageRecord,
    UsageScope,
)
from ai_multi_agent_platform.accounting.service import AccountingService
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .models import Membership, MembershipStatus, OrganizationStatus, TeamStatus
from .service import OrganizationService

DEFAULT_ACCOUNTING_AGGREGATE_POLICY_REF = "accounting.aggregate.read"


class OrganizationAccountingVisibility:
    """Resolve live #87 membership scope without making the final #15 decision.

    The Control Plane authorization provider still gates the request itself. This helper
    only prevents the accounting read model from widening that decision to stale or
    unrelated Organization/Team scopes. Cross-member aggregates require either canonical
    Organization administration or an explicit membership policy reference.
    """

    def __init__(
        self,
        organizations: OrganizationService,
        *,
        aggregate_policy_ref: str = DEFAULT_ACCOUNTING_AGGREGATE_POLICY_REF,
    ) -> None:
        if not aggregate_policy_ref.strip():
            raise ValueError("aggregate_policy_ref must not be blank")
        self._organizations = organizations
        self.aggregate_policy_ref = aggregate_policy_ref

    async def raw_record_visible(self, context: RequestContext, record: UsageRecord) -> bool:
        if _exact_owner(context, record.scope):
            return True
        if record.scope.owner_type == "organization" and record.scope.owner_id is not None:
            return await self.can_aggregate_organization(context, record.scope.owner_id)
        if record.scope.owner_type == "team" and record.scope.owner_id is not None:
            return await self.can_aggregate_team(context, record.scope.owner_id)
        return False

    async def budget_visible(self, context: RequestContext, budget: UsageBudget) -> bool:
        if _exact_budget_owner(context, budget):
            return True
        if budget.owner_type == "organization" and budget.owner_id is not None:
            return await self.can_aggregate_organization(context, budget.owner_id)
        if budget.owner_type == "team" and budget.owner_id is not None:
            return await self.can_aggregate_team(context, budget.owner_id)
        if budget.owner_type is not None:
            return False
        if budget.scope_type == "organization":
            return await self.can_aggregate_organization(context, budget.scope_id)
        if budget.scope_type == "team":
            return await self.can_aggregate_team(context, budget.scope_id)
        return False

    async def can_aggregate_organization(
        self,
        context: RequestContext,
        organization_id: str,
    ) -> bool:
        principal = context.actor.principal_ref
        try:
            organization = await self._organizations.repository.get_organization(organization_id)
        except LookupError:
            return False
        if organization.status is not OrganizationStatus.ACTIVE:
            return False
        if (
            principal == organization.owner_actor_id
            or principal in organization.administrator_actor_ids
        ):
            return True
        memberships = await self._active_memberships(principal, organization_id)
        return any(self.aggregate_policy_ref in item.policy_refs for item in memberships)

    async def can_aggregate_team(self, context: RequestContext, team_id: str) -> bool:
        principal = context.actor.principal_ref
        try:
            team = await self._organizations.repository.get_team(team_id)
            organization = await self._organizations.repository.get_organization(
                team.organization_id
            )
        except LookupError:
            return False
        if (
            team.status is not TeamStatus.ACTIVE
            or organization.status is not OrganizationStatus.ACTIVE
        ):
            return False
        if (
            principal == organization.owner_actor_id
            or principal in organization.administrator_actor_ids
        ):
            return True
        memberships = await self._active_memberships(principal, team.organization_id)
        return any(
            item.team_id == team_id and self.aggregate_policy_ref in item.policy_refs
            for item in memberships
        )

    async def visible_organization_ids(self, context: RequestContext) -> tuple[str, ...]:
        ids: list[str] = []
        for organization in await self._organizations.repository.list_organizations():
            if await self.can_aggregate_organization(context, organization.id):
                ids.append(organization.id)
        return tuple(sorted(ids))

    async def visible_team_ids(self, context: RequestContext) -> tuple[str, ...]:
        ids: list[str] = []
        for organization_id in await self.visible_organization_ids(context):
            for team in await self._organizations.repository.list_teams(organization_id):
                if await self.can_aggregate_team(context, team.id):
                    ids.append(team.id)
        principal = context.actor.principal_ref
        memberships = await self._organizations.repository.list_memberships(actor_id=principal)
        for membership in memberships:
            if (
                membership.status is MembershipStatus.ACTIVE
                and membership.team_id is not None
                and self.aggregate_policy_ref in membership.policy_refs
                and await self.can_aggregate_team(context, membership.team_id)
            ):
                ids.append(membership.team_id)
        return tuple(sorted(set(ids)))

    async def _active_memberships(
        self,
        actor_id: str,
        organization_id: str,
    ) -> tuple[Membership, ...]:
        memberships = await self._organizations.repository.list_memberships(
            actor_id=actor_id,
            organization_id=organization_id,
        )
        return tuple(item for item in memberships if item.status is MembershipStatus.ACTIVE)


class OrganizationUsageRecordResourceService:
    """Raw accounting records with exact-owner and explicit shared-owner visibility."""

    search_indexable = False

    def __init__(
        self,
        accounting: AccountingService,
        visibility: OrganizationAccountingVisibility,
    ) -> None:
        self._accounting = accounting
        self._visibility = visibility

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for record in self._accounting.query(UsageQuery()):
            if await self._visibility.raw_record_visible(context, record):
                resources.append(_record_resource(record))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        for record in self._accounting.query(UsageQuery()):
            if record.id == resource_id and await self._visibility.raw_record_visible(
                context, record
            ):
                return _record_resource(record)
        raise ContractError(ErrorCode.NOT_FOUND, f"usage record not found: {resource_id}")


class OrganizationUsageAggregateResourceService:
    """Personal plus policy-authorized Organization/Team aggregate projections."""

    def __init__(
        self,
        accounting: AccountingService,
        visibility: OrganizationAccountingVisibility,
        *,
        trend_window_seconds: int = 24 * 60 * 60,
        trend_bucket_seconds: int = 60 * 60,
    ) -> None:
        if trend_window_seconds <= 0 or trend_bucket_seconds <= 0:
            raise ValueError("trend window and bucket must be greater than zero")
        self._accounting = accounting
        self._visibility = visibility
        self._trend_window_seconds = trend_window_seconds
        self._trend_bucket_seconds = trend_bucket_seconds

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        all_records = self._accounting.query(UsageQuery())
        resources: list[dict[str, JsonValue]] = []

        personal = tuple(record for record in all_records if _exact_owner(context, record.scope))
        if context.actor.owner_type is not None and context.actor.owner_id is not None:
            resources.extend(
                _aggregate_resources(
                    personal,
                    UsageScope(
                        owner_type=context.actor.owner_type,
                        owner_id=context.actor.owner_id,
                    ),
                    trend_window_seconds=self._trend_window_seconds,
                    trend_bucket_seconds=self._trend_bucket_seconds,
                )
            )

        for organization_id in await self._visibility.visible_organization_ids(context):
            selected = tuple(
                _sanitize_for_organization(record, organization_id)
                for record in all_records
                if _record_in_organization(record, organization_id)
            )
            resources.extend(
                _aggregate_resources(
                    selected,
                    UsageScope(
                        organization_id=organization_id,
                        owner_type="organization",
                        owner_id=organization_id,
                    ),
                    trend_window_seconds=self._trend_window_seconds,
                    trend_bucket_seconds=self._trend_bucket_seconds,
                )
            )

        for team_id in await self._visibility.visible_team_ids(context):
            selected = tuple(
                _sanitize_for_team(record, team_id)
                for record in all_records
                if _record_in_team(record, team_id)
            )
            resources.extend(
                _aggregate_resources(
                    selected,
                    UsageScope(team_id=team_id, owner_type="team", owner_id=team_id),
                    trend_window_seconds=self._trend_window_seconds,
                    trend_bucket_seconds=self._trend_bucket_seconds,
                )
            )
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        for resource in await self.list_resources(context, PageQuery()):
            if resource["id"] == resource_id:
                return resource
        raise ContractError(ErrorCode.NOT_FOUND, f"usage aggregate not found: {resource_id}")


class OrganizationUsageBudgetResourceService:
    """Budget state with live membership/admin visibility and historical budget ownership."""

    def __init__(
        self,
        accounting: AccountingService,
        visibility: OrganizationAccountingVisibility,
    ) -> None:
        self._accounting = accounting
        self._visibility = visibility

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for budget in self._accounting.store.list_budgets():
            if await self._visibility.budget_visible(context, budget):
                resources.append(_budget_resource(self._accounting, budget))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        budget = self._accounting.store.get_budget(resource_id)
        if budget is None or not await self._visibility.budget_visible(context, budget):
            raise ContractError(ErrorCode.NOT_FOUND, f"usage budget not found: {resource_id}")
        return _budget_resource(self._accounting, budget)


def organization_accounting_resource_services(
    accounting: AccountingService,
    organizations: OrganizationService,
    *,
    aggregate_policy_ref: str = DEFAULT_ACCOUNTING_AGGREGATE_POLICY_REF,
) -> dict[str, ResourceService]:
    """Compose #76 read models with live #87 visibility; #15 remains the request gate."""

    visibility = OrganizationAccountingVisibility(
        organizations,
        aggregate_policy_ref=aggregate_policy_ref,
    )
    return {
        "usage-records": OrganizationUsageRecordResourceService(accounting, visibility),
        "usage-aggregates": OrganizationUsageAggregateResourceService(accounting, visibility),
        "usage-budgets": OrganizationUsageBudgetResourceService(accounting, visibility),
    }


def _exact_owner(context: RequestContext, scope: UsageScope) -> bool:
    actor = context.actor
    if actor.owner_type is None or actor.owner_id is None:
        return scope.owner_type is None and scope.owner_id is None
    return scope.owner_type == actor.owner_type and scope.owner_id == actor.owner_id


def _exact_budget_owner(context: RequestContext, budget: UsageBudget) -> bool:
    actor = context.actor
    if actor.owner_type is None or actor.owner_id is None:
        return budget.owner_type is None and budget.owner_id is None
    return budget.owner_type == actor.owner_type and budget.owner_id == actor.owner_id


def _record_in_organization(record: UsageRecord, organization_id: str) -> bool:
    return record.scope.organization_id == organization_id or (
        record.scope.owner_type == "organization" and record.scope.owner_id == organization_id
    )


def _record_in_team(record: UsageRecord, team_id: str) -> bool:
    return record.scope.team_id == team_id or (
        record.scope.owner_type == "team" and record.scope.owner_id == team_id
    )


def _sanitize_for_organization(record: UsageRecord, organization_id: str) -> UsageRecord:
    return replace(
        record,
        scope=replace(
            record.scope,
            owner_type="organization",
            owner_id=organization_id,
            organization_id=organization_id,
        ),
    )


def _sanitize_for_team(record: UsageRecord, team_id: str) -> UsageRecord:
    return replace(
        record,
        scope=replace(
            record.scope,
            owner_type="team",
            owner_id=team_id,
            team_id=team_id,
        ),
    )
