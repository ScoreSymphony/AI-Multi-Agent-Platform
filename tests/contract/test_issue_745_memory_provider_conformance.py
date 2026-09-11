from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    MemoryType,
)
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    AuthorizedDataMemoryProvider,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)
from ai_multi_agent_platform.testing import (
    FakeScopedMemoryProvider,
    assert_scoped_memory_provider_contract,
)


def _context() -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="corr-issue-745-memory-conformance",
            owner_type="user",
            owner_id="contract-user",
        ),
        actor_ref="user:contract-user",
    )


def _assert_fails(provider: FakeScopedMemoryProvider, message: str) -> None:
    with pytest.raises(AssertionError, match=message):
        asyncio.run(assert_scoped_memory_provider_contract(provider, _context()))


class IgnoresOwnerFilterProvider(FakeScopedMemoryProvider):
    async def query_entries(
        self,
        query: MemoryQuery,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        return await super().query_entries(replace(query, owner_ref=None), context)


def test_refined_memory_conformance_rejects_ignored_owner_filter() -> None:
    _assert_fails(IgnoresOwnerFilterProvider(), "scope and owner_ref")


class AppliesLimitBeforeCanonicalFiltersProvider(FakeScopedMemoryProvider):
    async def query_entries(
        self,
        query: MemoryQuery,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        if query.limit == 1 and query.owner_ref is not None and query.memory_types:
            limited = await super().query_entries(
                replace(query, owner_ref=None, memory_types=()),
                context,
            )
            return tuple(
                entry
                for entry in limited
                if entry.owner_ref == query.owner_ref and entry.memory_type in query.memory_types
            )
        return await super().query_entries(query, context)


def test_refined_memory_conformance_rejects_limit_before_canonical_filters() -> None:
    _assert_fails(
        AppliesLimitBeforeCanonicalFiltersProvider(),
        "scope, owner_ref and memory_types before limit",
    )


class RecordingScopedMemoryProvider(FakeScopedMemoryProvider):
    def __init__(self) -> None:
        super().__init__()
        self.query_calls: list[MemoryQuery] = []
        self.search_calls: list[MemoryQuery] = []

    async def query_entries(
        self,
        query: MemoryQuery,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        self.query_calls.append(query)
        return await super().query_entries(query, context)

    async def search_entries(
        self,
        query: MemoryQuery,
        text: str,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        self.search_calls.append(query)
        return await super().search_entries(query, text, context)


def _authorization_gate(*, allowed_actions: frozenset[AuthorizationAction]) -> AuthorizationGate:
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="user:contract-user",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=allowed_actions,
                resource_types=frozenset({ResourceType.MEMORY}),
            ),
        )
    )
    return AuthorizationGate(provider)


def test_authorized_memory_wrapper_forwards_complete_type_filtered_query() -> None:
    raw = RecordingScopedMemoryProvider()
    wrapped = AuthorizedDataMemoryProvider(
        raw,
        _authorization_gate(allowed_actions=frozenset({AuthorizationAction.READ})),
    )
    query = MemoryQuery(
        MemoryScope.TASK,
        "task_00000000-0000-0000-0000-000000000745",
        owner_ref="user:contract-user",
        include_superseded=True,
        limit=7,
        memory_types=(MemoryType.SEMANTIC, MemoryType.PROCEDURAL),
    )
    context = _context()

    assert asyncio.run(wrapped.query_entries(query, context)) == ()
    assert raw.query_calls[-1] == query

    assert asyncio.run(wrapped.search_entries(query, "contract marker", context)) == ()
    assert raw.search_calls[-1] == query


def test_type_filter_cannot_bypass_authorization_wrapper() -> None:
    raw = RecordingScopedMemoryProvider()
    wrapped = AuthorizedDataMemoryProvider(
        raw,
        _authorization_gate(allowed_actions=frozenset({AuthorizationAction.CREATE})),
    )
    query = MemoryQuery(
        MemoryScope.TASK,
        "task_00000000-0000-0000-0000-000000000745",
        memory_types=(MemoryType.SEMANTIC,),
    )

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(wrapped.query_entries(query, _context()))

    assert exc_info.value.code is ErrorCode.FORBIDDEN
    assert raw.query_calls == []
