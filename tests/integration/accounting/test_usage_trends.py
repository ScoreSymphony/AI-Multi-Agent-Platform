from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.accounting import (
    AccountingService,
    AggregationMode,
    InMemoryUsageStore,
    MeasurementQuality,
    UsageQuery,
    UsageRecord,
    UsageScope,
)
from ai_multi_agent_platform.accounting.control_plane import UsageAggregateResourceService
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext


def _record(
    *,
    timestamp: datetime,
    quantity: float | None,
    mode: AggregationMode = AggregationMode.ADDITIVE,
    quality: MeasurementQuality = MeasurementQuality.MEASURED,
    owner: str | None = None,
) -> UsageRecord:
    return UsageRecord(
        metric_type="test.metric",
        unit="units",
        quality=quality,
        source="test",
        quantity=quantity,
        timestamp=timestamp,
        aggregation_mode=mode,
        scope=UsageScope(
            owner_type=None if owner is None else "user",
            owner_id=owner,
        ),
    )


def test_additive_trend_buckets_sum_without_fabricating_empty_zeroes() -> None:
    start = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)
    service = AccountingService(InMemoryUsageStore())
    for record in (
        _record(timestamp=start + timedelta(minutes=10), quantity=1.0),
        _record(timestamp=start + timedelta(minutes=30), quantity=2.0),
        _record(
            timestamp=start + timedelta(hours=1, minutes=10),
            quantity=None,
            quality=MeasurementQuality.UNAVAILABLE,
        ),
        _record(timestamp=start + timedelta(hours=2, minutes=20), quantity=4.0),
    ):
        service.record(record)

    trend = service.trend(
        UsageQuery(
            metric_type="test.metric",
            unit="units",
            start=start,
            end=start + timedelta(hours=3),
        ),
        bucket_seconds=3600,
    )

    assert [bucket.total for bucket in trend] == [3.0, None, 4.0]
    assert [bucket.record_count for bucket in trend] == [2, 1, 1]
    assert [bucket.unavailable_count for bucket in trend] == [0, 1, 0]
    assert all(bucket.aggregation_mode is AggregationMode.ADDITIVE for bucket in trend)


def test_latest_trend_uses_latest_sample_per_bucket_without_carry_forward() -> None:
    start = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)
    service = AccountingService(InMemoryUsageStore())
    for record in (
        _record(
            timestamp=start + timedelta(minutes=5),
            quantity=10.0,
            mode=AggregationMode.LATEST,
        ),
        _record(
            timestamp=start + timedelta(minutes=50),
            quantity=12.0,
            mode=AggregationMode.LATEST,
        ),
        _record(
            timestamp=start + timedelta(hours=2, minutes=15),
            quantity=7.0,
            mode=AggregationMode.LATEST,
        ),
    ):
        service.record(record)

    trend = service.trend(
        UsageQuery(
            metric_type="test.metric",
            unit="units",
            start=start,
            end=start + timedelta(hours=3),
        ),
        bucket_seconds=3600,
    )

    assert [bucket.total for bucket in trend] == [12.0, None, 7.0]
    assert all(bucket.aggregation_mode is AggregationMode.LATEST for bucket in trend)


def test_trend_requires_bounded_reasonable_bucket_count() -> None:
    start = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)
    service = AccountingService(InMemoryUsageStore())
    with pytest.raises(ValueError, match="500 buckets"):
        service.trend(
            UsageQuery(
                metric_type="test.metric",
                unit="units",
                start=start,
                end=start + timedelta(seconds=501),
            ),
            bucket_seconds=1,
        )


def test_control_plane_trend_is_owner_isolated() -> None:
    now = datetime.now(UTC)
    accounting = AccountingService(InMemoryUsageStore())
    accounting.record(_record(timestamp=now - timedelta(minutes=30), quantity=5.0, owner="alice"))
    accounting.record(_record(timestamp=now - timedelta(minutes=20), quantity=999.0, owner="bob"))
    context = RequestContext(
        request_id="request-trend",
        correlation_id="correlation-trend",
        actor=ActorContext(
            principal_ref="user:alice",
            owner_type="user",
            owner_id="alice",
        ),
    )

    resources = asyncio.run(
        UsageAggregateResourceService(
            accounting, trend_window_seconds=3600, trend_bucket_seconds=900
        ).list_resources(context, PageQuery())
    )

    assert len(resources) == 1
    assert resources[0]["total"] == 5.0
    trend = resources[0]["trend"]
    assert isinstance(trend, list)
    values = [point["value"] for point in trend if isinstance(point, dict)]
    assert 5.0 in values
    assert 999.0 not in values
