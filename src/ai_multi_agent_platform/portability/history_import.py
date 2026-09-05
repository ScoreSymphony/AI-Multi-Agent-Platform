"""Historical-only Task import storage and mutation handler for issue #79."""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .history_codecs import (
    TASK_HISTORY_RESOURCE_TYPE,
    HistoricalTaskSnapshot,
)
from .models import IdPolicy, PortableResource
from .registry import ImportContext


@runtime_checkable
class HistoricalTaskArchiveRepository(Protocol):
    """Persistence boundary for imported history that is never a live kernel stream."""

    async def get(self, task_id: str) -> HistoricalTaskSnapshot: ...

    async def put(self, snapshot: HistoricalTaskSnapshot) -> None: ...

    async def delete(self, task_id: str) -> None: ...

    async def list_task_ids(self) -> tuple[str, ...]: ...


class InMemoryHistoricalTaskArchiveRepository(HistoricalTaskArchiveRepository):
    """Deterministic reference archive used by tests and single-process integrations."""

    def __init__(self) -> None:
        self._items: dict[str, HistoricalTaskSnapshot] = {}
        self._lock = asyncio.Lock()

    async def get(self, task_id: str) -> HistoricalTaskSnapshot:
        async with self._lock:
            try:
                return self._items[task_id]
            except KeyError as exc:
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    f"historical Task not found: {task_id}",
                ) from exc

    async def put(self, snapshot: HistoricalTaskSnapshot) -> None:
        task_id = snapshot.task.id
        async with self._lock:
            if task_id in self._items:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"historical Task already exists: {task_id}",
                )
            self._items[task_id] = snapshot

    async def delete(self, task_id: str) -> None:
        async with self._lock:
            if task_id not in self._items:
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    f"historical Task not found: {task_id}",
                )
            del self._items[task_id]

    async def list_task_ids(self) -> tuple[str, ...]:
        async with self._lock:
            return tuple(sorted(self._items))


class TaskHistoryImportMutationHandler:
    """Import Task history only into an archive, never into the live EventRepository."""

    resource_type = TASK_HISTORY_RESOURCE_TYPE

    def __init__(self, archive: HistoricalTaskArchiveRepository) -> None:
        self._archive = archive

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del context
        snapshot = _require_history(value)
        if resource.id_policy is not IdPolicy.HISTORICAL_PRESERVE:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "historical Task import requires historical_preserve identity semantics",
            )
        if snapshot.task.id != resource.resource_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "historical Task decoded identity differs from its portable resource ID",
            )
        try:
            await self._archive.get(snapshot.task.id)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return
            raise
        raise ContractError(
            ErrorCode.CONFLICT,
            f"historical Task appeared after import preview: {snapshot.task.id}",
        )

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, context
        snapshot = _require_history(value)
        await self._archive.put(snapshot)
        return snapshot.task.id

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context
        if not isinstance(token, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "historical Task rollback token must be the archived Task ID",
            )
        await self._archive.delete(token)


def _require_history(value: object) -> HistoricalTaskSnapshot:
    if not isinstance(value, HistoricalTaskSnapshot):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "historical Task mutation handler received the wrong decoded resource type",
        )
    return value
