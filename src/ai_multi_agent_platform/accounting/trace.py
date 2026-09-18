"""Read-only accounting adapter for the trace explorer."""

from __future__ import annotations

from ai_multi_agent_platform.observability.trace import TraceUsageRecord

from .models import UsageQuery, UsageScope
from .service import AccountingService


class AccountingTraceUsageReader:
    """Expose canonical UsageRecords without recomputing or estimating accounting truth."""

    def __init__(self, accounting: AccountingService) -> None:
        self._accounting = accounting

    def query_trace_usage(self, *, task_id: str) -> tuple[TraceUsageRecord, ...]:
        records = self._accounting.query(UsageQuery(scope=UsageScope(task_id=task_id)))
        return tuple(
            TraceUsageRecord(
                id=record.id,
                metric_type=record.metric_type,
                unit=record.unit,
                quality=record.quality.value,
                source=record.source,
                timestamp=record.timestamp,
                scope=record.scope.fields(),
                quantity=record.quantity,
                provider=record.provider,
                cost_amount=record.cost_amount,
                currency=record.currency,
                correlation_id=record.correlation_id,
                causation_id=record.causation_id,
                precision=record.precision,
                confidence=record.confidence,
                provenance=dict(record.provenance),
            )
            for record in records
        )
