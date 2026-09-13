"""Awaitable Security persistence adapters for approval and authorization-audit runtime paths."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Literal, Protocol, TypeVar

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .approvals import ApprovalRecord, ApprovalService
from .authorization import AuthorizationAuditRecord, ProposedAction, RiskClassification

_T = TypeVar("_T")
_SerializationKey = Literal["approvals", "audit"]

_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class AsyncApprovalService(Protocol):
    """Backend-neutral awaitable Approval lifecycle used by async runtime callers."""

    async def get(self, approval_id: str) -> ApprovalRecord: ...

    async def all(self) -> tuple[ApprovalRecord, ...]: ...

    async def resolve_valid_for(
        self,
        action: ProposedAction,
        *,
        approval_id: str | None = None,
    ) -> ApprovalRecord | None: ...

    async def ensure_pending(
        self,
        action: ProposedAction,
        *,
        reason: str,
        policy_id: str,
        risk: RiskClassification = RiskClassification.ELEVATED,
        expires_at: datetime | None = None,
    ) -> tuple[ApprovalRecord, bool]: ...

    async def decide_authorized(
        self,
        approval_id: str,
        *,
        approver_ref: str,
        approve: bool,
        comment: str | None = None,
    ) -> ApprovalRecord: ...

    async def cancel_authorized(
        self,
        approval_id: str,
        *,
        actor_ref: str,
    ) -> ApprovalRecord: ...


class AsyncAuthorizationAuditSink(Protocol):
    """Awaitable append-only authorization-audit persistence boundary."""

    async def append(self, record: AuthorizationAuditRecord) -> None: ...


class SecurityPersistenceOffload:
    """Bound Security-owned blocking persistence outside asyncio's shared executor.

    Approval lifecycle operations and authorization-audit appends share one bounded executor but
    use separate serialization domains because they target independent SQLite databases. Approval
    reads are serialized with writes because reading can durably expire a pending Approval.
    """

    def __init__(self, *, max_concurrency: int = 4) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="security-persistence",
        )
        self._serialization_locks: dict[_SerializationKey, threading.Lock] = {
            "approvals": threading.Lock(),
            "audit": threading.Lock(),
        }

    async def run(
        self,
        operation: Callable[[], _T],
        *,
        serialization: _SerializationKey | None = None,
    ) -> _T:
        loop = asyncio.get_running_loop()
        worker = loop.run_in_executor(
            self._executor,
            self._run_sync,
            operation,
            serialization,
        )
        return await _await_persistence_boundary(worker)

    def _run_sync(
        self,
        operation: Callable[[], _T],
        serialization: _SerializationKey | None,
    ) -> _T:
        if serialization is None:
            return operation()
        with self._serialization_locks[serialization]:
            return operation()


async def _await_persistence_boundary[T](worker: asyncio.Future[T]) -> T:
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
        failure = worker.exception()
        if failure is not None:
            raise failure from None
        raise


def _map_sqlite_error(exc: sqlite3.Error, message: str) -> ContractError:
    if isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).casefold() for marker in _BUSY_MARKERS
    ):
        return ContractError(
            ErrorCode.TRANSIENT_FAILURE,
            message,
            retryable=True,
        )
    return ContractError(ErrorCode.BACKEND_ERROR, message)


def _map_contract_sqlite_error(exc: ContractError, message: str) -> ContractError:
    cause = exc.__cause__
    if (
        exc.code is ErrorCode.BACKEND_ERROR
        and isinstance(cause, sqlite3.OperationalError)
        and any(marker in str(cause).casefold() for marker in _BUSY_MARKERS)
    ):
        return ContractError(
            ErrorCode.TRANSIENT_FAILURE,
            message,
            retryable=True,
        )
    return exc


class _AsyncAdapterBase:
    def __init__(self, *, offload: SecurityPersistenceOffload | None = None) -> None:
        self._offload = offload or SecurityPersistenceOffload()

    async def _run[T](
        self,
        operation: Callable[[], T],
        *,
        message: str,
        serialization: _SerializationKey | None = None,
    ) -> T:
        try:
            return await self._offload.run(operation, serialization=serialization)
        except ContractError as exc:
            mapped = _map_contract_sqlite_error(exc, message)
            if mapped is exc:
                raise
            raise mapped from exc
        except sqlite3.Error as exc:
            raise _map_sqlite_error(exc, message) from exc


class AsyncApprovalServiceAdapter(_AsyncAdapterBase):
    """Awaitable facade over the existing synchronous Approval lifecycle contract."""

    def __init__(
        self,
        approvals: ApprovalService,
        *,
        offload: SecurityPersistenceOffload | None = None,
    ) -> None:
        super().__init__(offload=offload)
        self._approvals = approvals

    async def get(self, approval_id: str) -> ApprovalRecord:
        return await self._run(
            lambda: self._approvals.get(approval_id),
            message="failed to read approval",
            serialization="approvals",
        )

    async def all(self) -> tuple[ApprovalRecord, ...]:
        return await self._run(
            self._approvals.all,
            message="failed to list approvals",
            serialization="approvals",
        )

    async def resolve_valid_for(
        self,
        action: ProposedAction,
        *,
        approval_id: str | None = None,
    ) -> ApprovalRecord | None:
        def resolve() -> ApprovalRecord | None:
            if approval_id is not None:
                if not self._approvals.valid_for(approval_id, action):
                    return None
                return self._approvals.get(approval_id)
            return self._approvals.find_valid_for(action)

        return await self._run(
            resolve,
            message="failed to resolve approval",
            serialization="approvals",
        )

    async def ensure_pending(
        self,
        action: ProposedAction,
        *,
        reason: str,
        policy_id: str,
        risk: RiskClassification = RiskClassification.ELEVATED,
        expires_at: datetime | None = None,
    ) -> tuple[ApprovalRecord, bool]:
        def ensure() -> tuple[ApprovalRecord, bool]:
            existing = self._approvals.pending_for(action)
            if existing is not None:
                return existing, False
            return (
                self._approvals.request(
                    action,
                    reason=reason,
                    policy_id=policy_id,
                    risk=risk,
                    expires_at=expires_at,
                ),
                True,
            )

        return await self._run(
            ensure,
            message="failed to persist pending approval",
            serialization="approvals",
        )

    async def decide_authorized(
        self,
        approval_id: str,
        *,
        approver_ref: str,
        approve: bool,
        comment: str | None = None,
    ) -> ApprovalRecord:
        return await self._run(
            lambda: self._approvals._decide_authorized(
                approval_id,
                approver_ref=approver_ref,
                approve=approve,
                comment=comment,
            ),
            message="failed to persist approval decision",
            serialization="approvals",
        )

    async def cancel_authorized(
        self,
        approval_id: str,
        *,
        actor_ref: str,
    ) -> ApprovalRecord:
        return await self._run(
            lambda: self._approvals._cancel_authorized(
                approval_id,
                actor_ref=actor_ref,
            ),
            message="failed to persist approval cancellation",
            serialization="approvals",
        )


class AsyncAuthorizationAuditSinkAdapter(_AsyncAdapterBase):
    """Awaitable facade over an existing synchronous authorization-audit sink."""

    def __init__(
        self,
        sink: Callable[[AuthorizationAuditRecord], None],
        *,
        offload: SecurityPersistenceOffload | None = None,
    ) -> None:
        super().__init__(offload=offload)
        self._sink = sink

    async def append(self, record: AuthorizationAuditRecord) -> None:
        await self._run(
            lambda: self._sink(record),
            message="failed to persist authorization audit record",
            serialization="audit",
        )


__all__ = [
    "AsyncApprovalService",
    "AsyncApprovalServiceAdapter",
    "AsyncAuthorizationAuditSink",
    "AsyncAuthorizationAuditSinkAdapter",
    "SecurityPersistenceOffload",
]
