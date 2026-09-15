"""Backend-neutral asyncio-safe runtime boundary for Decision Records."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Protocol, TypeVar, cast

from ai_multi_agent_platform.contracts.types import JsonValue

from .async_persistence import DecisionPersistenceOffload, decision_persistence_offload
from .models import DecisionRecord, DecisionRecordView, DecisionReference
from .service import DecisionService

_T = TypeVar("_T")
_ASYNC_METHODS = (
    "create",
    "supersede",
    "withdraw",
    "link_downstream_provenance",
    "action_provenance",
    "view",
    "list_views",
    "supersession_chain",
)


class AsyncDecisionService(Protocol):
    """Awaitable service contract consumed by runtime/control-plane callers."""

    async def create(self, record: DecisionRecord) -> DecisionRecordView: ...

    async def supersede(
        self,
        previous_id: str,
        replacement: DecisionRecord,
    ) -> DecisionRecordView: ...

    async def withdraw(
        self,
        decision_record_id: str,
        *,
        actor_ref: str,
        reason: str,
    ) -> DecisionRecordView: ...

    async def link_downstream_provenance(
        self,
        decision_record_id: str,
        reference: DecisionReference,
    ) -> DecisionRecordView: ...

    async def action_provenance(self, decision_record_id: str) -> dict[str, JsonValue]: ...

    async def view(self, decision_record_id: str) -> DecisionRecordView: ...

    async def list_views(self) -> tuple[DecisionRecordView, ...]: ...

    async def supersession_chain(
        self,
        decision_record_id: str,
    ) -> tuple[DecisionRecordView, ...]: ...


class AsyncDecisionRuntime:
    """Adapt the synchronous compatibility service onto bounded persistence workers."""

    def __init__(
        self,
        service: DecisionService,
        *,
        persistence_offload: DecisionPersistenceOffload | None = None,
    ) -> None:
        self.service = service
        self.repository = service.repository
        self._offload = decision_persistence_offload(
            self.repository,
            owner=self,
            requested=persistence_offload,
        )

    async def _run(self, operation: Callable[[], _T], *, message: str) -> _T:
        return await self._offload.run(operation, message=message)

    async def create(self, record: DecisionRecord) -> DecisionRecordView:
        return await self._run(
            lambda: self.service.create(record),
            message="failed to create DecisionRecord",
        )

    async def supersede(
        self,
        previous_id: str,
        replacement: DecisionRecord,
    ) -> DecisionRecordView:
        return await self._run(
            lambda: self.service.supersede(previous_id, replacement),
            message="failed to supersede DecisionRecord",
        )

    async def withdraw(
        self,
        decision_record_id: str,
        *,
        actor_ref: str,
        reason: str,
    ) -> DecisionRecordView:
        return await self._run(
            lambda: self.service.withdraw(
                decision_record_id,
                actor_ref=actor_ref,
                reason=reason,
            ),
            message="failed to withdraw DecisionRecord",
        )

    async def link_downstream_provenance(
        self,
        decision_record_id: str,
        reference: DecisionReference,
    ) -> DecisionRecordView:
        return await self._run(
            lambda: self.service.link_downstream_provenance(decision_record_id, reference),
            message="failed to link DecisionRecord provenance",
        )

    async def action_provenance(self, decision_record_id: str) -> dict[str, JsonValue]:
        return await self._run(
            lambda: self.service.action_provenance(decision_record_id),
            message="failed to read DecisionRecord action provenance",
        )

    async def view(self, decision_record_id: str) -> DecisionRecordView:
        return await self._run(
            lambda: self.service.view(decision_record_id),
            message="failed to read DecisionRecord",
        )

    async def list_views(self) -> tuple[DecisionRecordView, ...]:
        return await self._run(
            self.service.list_views,
            message="failed to list DecisionRecords",
        )

    async def supersession_chain(
        self,
        decision_record_id: str,
    ) -> tuple[DecisionRecordView, ...]:
        return await self._run(
            lambda: self.service.supersession_chain(decision_record_id),
            message="failed to read DecisionRecord supersession chain",
        )


def as_async_decision_service(
    decisions: DecisionService | AsyncDecisionService,
) -> AsyncDecisionService:
    """Keep native async services native; adapt the synchronous compatibility service once."""

    states = tuple(_method_is_async(decisions, name) for name in _ASYNC_METHODS)
    if any(states) and not all(states):
        raise TypeError("Decision service must be consistently sync or async")
    if all(states):
        return cast(AsyncDecisionService, decisions)
    return AsyncDecisionRuntime(cast(DecisionService, decisions))


def _method_is_async(value: object, name: str) -> bool:
    return inspect.iscoroutinefunction(getattr(value, name, None))


__all__ = [
    "AsyncDecisionRuntime",
    "AsyncDecisionService",
    "as_async_decision_service",
]
