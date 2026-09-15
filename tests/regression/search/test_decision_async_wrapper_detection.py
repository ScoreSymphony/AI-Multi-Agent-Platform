from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

from ai_multi_agent_platform.decisions import AsyncDecisionService, as_async_decision_service


def _regular_async_wrapper[T](
    operation: Callable[..., Awaitable[T]],
) -> Callable[..., Awaitable[T]]:
    def wrapped(*args: Any, **kwargs: Any) -> Awaitable[T]:
        return operation(*args, **kwargs)

    return wrapped


class _DecoratedAsyncDecisionService:
    @_regular_async_wrapper
    async def create(self, record: Any) -> Any:
        raise AssertionError(record)

    @_regular_async_wrapper
    async def supersede(self, previous_id: str, replacement: Any) -> Any:
        raise AssertionError(previous_id, replacement)

    @_regular_async_wrapper
    async def withdraw(self, decision_record_id: str, *, actor_ref: str, reason: str) -> Any:
        raise AssertionError(decision_record_id, actor_ref, reason)

    @_regular_async_wrapper
    async def link_downstream_provenance(self, decision_record_id: str, reference: Any) -> Any:
        raise AssertionError(decision_record_id, reference)

    @_regular_async_wrapper
    async def action_provenance(self, decision_record_id: str) -> dict[str, object]:
        raise AssertionError(decision_record_id)

    @_regular_async_wrapper
    async def view(self, decision_record_id: str) -> Any:
        raise AssertionError(decision_record_id)

    @_regular_async_wrapper
    async def list_views(self) -> tuple[()]:
        return ()

    @_regular_async_wrapper
    async def supersession_chain(self, decision_record_id: str) -> tuple[()]:
        raise AssertionError(decision_record_id)


def test_regular_decorators_do_not_force_async_decisions_through_sync_runtime() -> None:
    service = _DecoratedAsyncDecisionService()
    resolved = as_async_decision_service(cast(AsyncDecisionService, service))

    assert resolved is service
    assert asyncio.run(resolved.list_views()) == ()
