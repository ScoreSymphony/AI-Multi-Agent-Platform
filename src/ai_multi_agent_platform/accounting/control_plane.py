"""Control Plane resource services for usage/accounting state."""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .models import (
    UsageAggregate,
    UsageBudget,
    UsageQuery,
    UsageRecord,
    UsageScope,
    utc_now,
)
from .service import AccountingService, aggregate_usage_records, trend_usage_records


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

    def __init__(
        self,
        accounting: AccountingService,
        *,
        trend_window_seconds: int = 24 * 60 * 60,
        trend_bucket_seconds: int = 60 * 60,
    ) -> None:
        if trend_window_seconds <= 0 or trend_bucket_seconds <= 0:
            raise ValueError("trend window and bucket must be greater than zero")
        self._accounting = accounting
        self._trend_window_seconds = trend_window_seconds
        self._trend_bucket_seconds = trend_bucket_seconds

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
        trend_end = utc_now()
        trend_start = trend_end - timedelta(seconds=self._trend_window_seconds)
        for metric_type, unit in pairs:
            selected = _metric_records(records, metric_type, unit)
            aggregate = aggregate_usage_records(selected, metric_type=metric_type, unit=unit)
            trend = trend_usage_records(
                selected,
                metric_type=metric_type,
                unit=unit,
                start=trend_start,
                end=trend_end,
                bucket_seconds=self._trend_bucket_seconds,
            )
            resources.append(
                _aggregate_resource(
                    metric_type,
                    unit,
                    aggregate,
                    scope_key,
                    trend=trend,
                    trend_start=trend_start,
                    trend_end=trend_end,
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


def _metric_records(
    records: tuple[UsageRecord, ...], metric_type: str, unit: str
) -> tuple[UsageRecord, ...]:
    return tuple(
        record for record in records if record.metric_type == metric_type and record.unit == unit
    )


def _aggregate_visible(
    records: tuple[UsageRecord, ...], metric_type: str, unit: str
) -> UsageAggregate:
    selected = _metric_records(records, metric_type, unit)
    return aggregate_usage_records(selected, metric_type=metric_type, unit=unit)


def _record_resource(record: UsageRecord) -> dict[str, JsonValue]:
    scope: dict[str, JsonValue] = {}
    for key, value in record.scope.fields().items():
        scope[key] = value
    return {
        "id": record.id,
        "metric_type": record.metric_type,
        "quantity": record.quantity,
        "unit": record.unit,
        "quality": record.quality.value,
        "aggregation_mode": record.aggregation_mode.value,
        "source": record.source,
        "provider": record.provider,
        "timestamp": record.timestamp.isoformat(),
        "started_at": None if record.started_at is None else record.started_at.isoformat(),
        "ended_at": None if record.ended_at is None else record.ended_at.isoformat(),
        "scope": scope,
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
    *,
    trend: tuple[UsageAggregate, ...] = (),
    trend_start: datetime | None = None,
    trend_end: datetime | None = None,
    trend_bucket_seconds: int | None = None,
) -> dict[str, JsonValue]:
    digest = sha256(f"{scope_key}\0{metric_type}\0{unit}".encode()).hexdigest()[:24]
    quality_counts: dict[str, JsonValue] = {
        quality.value: count for quality, count in aggregate.quality_counts.items()
    }
    trend_points: list[JsonValue] = []
    for point in trend:
        point_quality: dict[str, JsonValue] = {
            quality.value: count for quality, count in point.quality_counts.items()
        }
        trend_points.append(
            {
                "start": None if point.start is None else point.start.isoformat(),
                "end": None if point.end is None else point.end.isoformat(),
                "value": point.total,
                "record_count": point.record_count,
                "unavailable_count": point.unavailable_count,
                "quality_counts": point_quality,
            }
        )
    return {
        "id": f"usage_aggregate_{digest}",
        "metric_type": metric_type,
        "unit": unit,
        "total": aggregate.total,
        "record_count": aggregate.record_count,
        "unavailable_count": aggregate.unavailable_count,
        "quality_counts": quality_counts,
        "aggregation_mode": aggregate.aggregation_mode.value,
        "trend_window_start": None if trend_start is None else trend_start.isoformat(),
        "trend_window_end": None if trend_end is None else trend_end.isoformat(),
        "trend_bucket_seconds": trend_bucket_seconds,
        "trend": trend_points,
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
