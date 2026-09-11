from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryQuery,
    MemoryType,
)
from ai_multi_agent_platform.testing import (
    FakeScopedMemoryProvider,
    assert_scoped_memory_provider_contract,
)


def _context() -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="corr-scoped-memory-conformance",
            owner_type="user",
            owner_id="contract-user",
        ),
        actor_ref="user:contract-user",
    )


def _assert_fails(provider: FakeScopedMemoryProvider, message: str) -> None:
    with pytest.raises(AssertionError, match=message):
        asyncio.run(assert_scoped_memory_provider_contract(provider, _context()))


def test_refined_memory_conformance_accepts_reusable_in_memory_provider() -> None:
    asyncio.run(assert_scoped_memory_provider_contract(FakeScopedMemoryProvider(), _context()))


def test_refined_memory_conformance_accepts_local_reference_provider(tmp_path: Path) -> None:
    provider = LocalMemoryProvider(tmp_path / "memory.sqlite3")
    asyncio.run(assert_scoped_memory_provider_contract(provider, _context()))


def test_refined_memory_conformance_accepts_explicitly_unsupported_optional_capabilities() -> None:
    provider = FakeScopedMemoryProvider(
        supports_exact_expiry=False,
        supports_discovery=False,
    )
    asyncio.run(assert_scoped_memory_provider_contract(provider, _context()))


class DropsMemoryTypeProvider(FakeScopedMemoryProvider):
    async def write_entry(self, entry: MemoryEntry, context: DataAccessContext) -> MemoryEntry:
        return await super().write_entry(
            replace(entry, memory_type=MemoryType.UNCLASSIFIED),
            context,
        )


def test_refined_memory_conformance_rejects_type_loss() -> None:
    _assert_fails(DropsMemoryTypeProvider(), "changed canonical memory_type")


class IgnoresExactTypeFilterProvider(FakeScopedMemoryProvider):
    async def query_entries(
        self,
        query: MemoryQuery,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        return await super().query_entries(replace(query, memory_types=()), context)


def test_refined_memory_conformance_rejects_ignored_exact_type_filter() -> None:
    _assert_fails(IgnoresExactTypeFilterProvider(), "exact memory_type filtering")


class IgnoresMultiTypeFilterProvider(FakeScopedMemoryProvider):
    async def query_entries(
        self,
        query: MemoryQuery,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        if len(query.memory_types) > 1:
            query = replace(query, memory_types=())
        return await super().query_entries(query, context)


def test_refined_memory_conformance_rejects_ignored_multi_type_filter() -> None:
    _assert_fails(IgnoresMultiTypeFilterProvider(), "multi-type filtering")


class IgnoresSearchTypeFilterProvider(FakeScopedMemoryProvider):
    async def search_entries(
        self,
        query: MemoryQuery,
        text: str,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        return await super().search_entries(replace(query, memory_types=()), text, context)


def test_refined_memory_conformance_rejects_search_filter_mismatch() -> None:
    _assert_fails(IgnoresSearchTypeFilterProvider(), "memory search must honor")


class LeaksPrivateTypeProvider(FakeScopedMemoryProvider):
    async def write_entry(self, entry: MemoryEntry, context: DataAccessContext) -> MemoryEntry:
        stored = await super().write_entry(entry, context)
        object.__setattr__(stored, "memory_type", "provider-native")
        return stored


def test_refined_memory_conformance_rejects_provider_private_type_leak() -> None:
    _assert_fails(LeaksPrivateTypeProvider(), "provider-private type identifier")


class CorruptsDiscoveryTypeProvider(FakeScopedMemoryProvider):
    async def list_entries_for_discovery(self) -> tuple[MemoryEntry, ...]:
        entries = await super().list_entries_for_discovery()
        if not entries:
            return entries
        corrupted = replace(entries[0])
        object.__setattr__(corrupted, "memory_type", "provider-native")
        return (corrupted, *entries[1:])


def test_refined_memory_conformance_rejects_advertised_discovery_type_corruption() -> None:
    _assert_fails(CorruptsDiscoveryTypeProvider(), "memory discovery leaked")
