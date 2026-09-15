from __future__ import annotations

import asyncio
import weakref
from typing import cast

import pytest

from ai_multi_agent_platform.accounting import (
    AccountingService,
    AsyncAccountingServiceAdapter,
    InMemoryUsageStore,
    MeasurementQuality,
    UsageQuery,
    UsageRecord,
)
from ai_multi_agent_platform.accounting.store import UsageStore


class _OpaqueUsageStore:
    """Protocol-compatible store that is unhashable and cannot be weak-referenced."""

    __slots__ = ("_inner",)
    __hash__ = None

    def __init__(self) -> None:
        self._inner = InMemoryUsageStore()

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._inner, name)


def test_accounting_adapter_supports_unhashable_nonweakref_store() -> None:
    store = _OpaqueUsageStore()
    with pytest.raises(TypeError):
        weakref.ref(store)

    adapter = AsyncAccountingServiceAdapter(AccountingService(cast(UsageStore, store)))
    record = UsageRecord(
        metric_type="runtime.test.count",
        unit="count",
        quality=MeasurementQuality.MEASURED,
        source="accounting-nonweakref-regression",
        quantity=1.0,
    )

    async def scenario() -> None:
        await adapter.record(record)
        assert await adapter.query(UsageQuery()) == (record,)

    asyncio.run(scenario())
