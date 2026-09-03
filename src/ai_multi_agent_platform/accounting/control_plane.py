"""Control Plane resource services for usage/accounting state."""

from __future__ import annotations

from hashlib import sha256

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .models import (
    MeasurementQuality,
    UsageAggregate,
    UsageBudget,
    UsageQuery,
    UsageRecord,
    UsageScope,
)
from .service import AccountingService


class UsageRecordResourceService:
    """Read-only canonical usage records with explicit owner isolation."""

    def __init__(self, accounting: AccountingService) -> None:
        self._accounting = accounting

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        records = self._accounting.query(_owner_query(context))
        return tuple(_record_resource(record) for record in records if _visible(record, context))

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        for record in self._accounting.query(_owner_query(context)):
            if record.id == resource_id and _visible(record, context):
                return _record_resource(record)
        raise ContractError(ErrorCode.NOT_FOUND, f"usage record not found: {resource_id}")


class UsageAggregateResourceService:
    """Current aggregate view grouped by metric/unit for the visible accounting scope."""

    def __init__(self, accounting: AccountingService) -> None:
        self._accounting = accounting

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        records = tuple(
            record
            for record in self._accounting.query(_owner_query(context))
            if _visible(record, context)
        )
        pairs = sorted({(record.metric_type, record.unit) for record in records})
        resources: list[dict[str, JsonValue]] = []
        scope_key = _scope_key(context)
        for metric_type, unit in pairs:
            aggregate = _aggregate_visible(records, metric_type, unit)
            resources.append(_aggregate_resource(metric_type, unit, aggregate, scope_key))
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


class UsageBudgetResourceService:
    """Read-only budget state; mutations remain explicit domain commands."""

    def __init__(self, accounting: AccountingService) -> None:
        self._accounting = accounting

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        resources: list[dict[str, JsonValue]] = []
        for budget in self._accounting.store.list_budgets():
            if _budget_visible(budget, context):
                resources.append(_budget_resource(self._accounting, budget))
        return tuple(resources)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        budget = self._accounting.store.get_budget(resource_id)
        if budget is None or not _budget_visible(budget, context):
            raise ContractError(ErrorCode.NOT_FOUND, f"usage budget not found: {resource_id}")
        return _budget_resource(self._accounting, budget)


def accounting_resource_services(
    accounting: AccountingService,
) -> dict[
    str,
    UsageRecordResourceService | UsageAggregateResourceService | UsageBudgetResourceService,
]:
    """Registrations for #32 without making the Control Plane own accounting state."""

    return {
        "usage-records": UsageRecordResourceService(accounting),
        "usage-aggregates": UsageAggregateResourceService(accounting),
        "usage-budgets": UsageBudgetResourceService(accounting),
    }


def _owner_query(context: RequestContext) -> UsageQuery:
    actor = context.actor
    if actor.owner_type is None or actor.owner_id is None:
        return UsageQuery()
    return UsageQuery(scope=UsageScope(owner_type=actor.owner_type, owner_id=actor.owner_id))


def _visible(record: UsageRecord, context: RequestContext) -> bool:
    actor = context.actor
    if actor.owner_type is None or actor.owner_id is None:
        return record.scope.owner_type is None and record.scope.owner_id is None
    return record.scope.owner_type == actor.owner_type and record.scope.owner_id == actor.owner_id


def _budget_visible(budget: UsageBudget, context: RequestContext) -> bool:
    actor = context.actor
    if actor.owner_type is None or actor.owner_id is None:
        return budget.owner_type is None and budget.owner_id is None
    return budget.owner_type == actor.owner_type and budget.owner_id == actor.owner_id


def _scope_key(context: RequestContext) -> str:
    actor = context.actor
    if actor.owner_type is None or actor.owner_id is None:
        return "unowned"
    return f"{actor.owner_type}:{actor.owner_id}"


def _aggregate_visible(
    records: tuple[UsageRecord, ...], metric_type: str, unit: str
) -> UsageAggregate:
    selected = tuple(
        record for record in records if record.metric_type == metric_type and record.unit == unit
    )
    values = [record.quantity for record in selected if record.quantity is not None]
    quality_counts = {quality: 0 for quality in MeasurementQuality}
    for record in selected:
        quality_counts[record.quality] += 1
    return UsageAggregate(
        metric_type=metric_type,
        unit=unit,
        total=sum(values) if values else None,
        record_count=len(selected),
        unavailable_count=quality_counts[MeasurementQuality.UNAVAILABLE],
        quality_counts=quality_counts,
    )


def _record_resource(record: UsageRecord) -> dict[str, JsonValue]:
    return {
        "id": record.id,
        "metric_type": record.metric_type,
        "quantity": record.quantity,
        "unit": record.unit,
        "quality": record.quality.value,
        "source": record.source,
        "provider": record.provider,
        "timestamp": record.timestamp.isoformat(),
        "started_at": None if record.started_at is None else record.started_at.isoformat(),
        "ended_at": None if record.ended_at is None else record.ended_at.isoformat(),
        "scope": record.scope.fields(),
        "correlation_id": record.correlation_id,
        "causation_id": record.causation_id,
        "cost_amount": record.cost_amount,
        "currency": record.currency,
        "precision": record.precision,
        "confidence": record.confidence,
        "provenance": dict(record.provenance),
    }


def _aggregate_resource(
    metric_type: str,
    unit: str,
    aggregate: UsageAggregate,
    scope_key: str,
) -> dict[str, JsonValue]:
    digest = sha256(f"{scope_key}\0{metric_type}\0{unit}".encode()).hexdigest()[:24]
    quality_counts: dict[str, JsonValue] = {
        quality.value: count for quality, count in aggregate.quality_counts.items()
    }
    return {
        "id": f"usage_aggregate_{digest}",
        "metric_type": metric_type,
        "unit": unit,
        "total": aggregate.total,
        "record_count": aggregate.record_count,
        "unavailable_count": aggregate.unavailable_count,
        "quality_counts": quality_counts,
    }


def _budget_resource(accounting: AccountingService, budget: UsageBudget) -> dict[str, JsonValue]:
    state = accounting.budget_state(budget.id)
    return {
        "id": budget.id,
        "metric_type": budget.metric_type,
        "unit": budget.unit,
        "scope_type": budget.scope_type,
        "scope_id": budget.scope_id,
        "limit": budget.limit,
        "kind": budget.kind.value,
        "action": budget.action.value,
        "warning_fraction": budget.warning_fraction,
        "window_seconds": budget.window_seconds,
        "include_estimated": budget.include_estimated,
        "owner_type": budget.owner_type,
        "owner_id": budget.owner_id,
        "version": budget.version,
        "consumed": state.consumed,
        "remaining": state.remaining,
        "fraction": state.fraction,
        "threshold_level": None if state.level is None else state.level.value,
    }
