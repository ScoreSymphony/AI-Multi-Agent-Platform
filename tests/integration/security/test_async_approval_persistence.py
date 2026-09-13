"""Async Approval/authorization-audit persistence regressions for issue #892."""

from __future__ import annotations

import asyncio
import gc
import sqlite3
import threading
import time
import weakref
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from ai_multi_agent_platform.contracts import (
    AuthorizationOutcome,
    ContractError,
    ErrorCode,
    OperationContext,
)
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.domain import ApprovalStatus, new_id
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    ApprovalService,
    AsyncApprovalServiceAdapter,
    AsyncAuthorizationAuditSinkAdapter,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ProposedAction,
    ResourceType,
    SecurityPersistenceOffload,
    SqliteApprovalService,
    SqliteAuthorizationAuditSink,
)
from ai_multi_agent_platform.security.approval_control_plane import ApprovalResourceService


class _RecordingApprovalService(SqliteApprovalService):
    def __init__(self, path: Path, *, delay: float = 0.05) -> None:
        self.connection_threads: list[int] = []
        self.delay_connections = False
        self._delay = delay
        super().__init__(path)
        self.delay_connections = True

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.get_ident())
        if self.delay_connections:
            time.sleep(self._delay)
        return super()._connect()


class _RecordingAuditSink(SqliteAuthorizationAuditSink):
    def __init__(self, path: Path, *, delay: float = 0.05) -> None:
        self.connection_threads: list[int] = []
        self.delay_connections = False
        self._delay = delay
        super().__init__(path)
        self.delay_connections = True

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.get_ident())
        if self.delay_connections:
            time.sleep(self._delay)
        return super()._connect()


class _BlockingApprovalService(SqliteApprovalService):
    def __init__(self, path: Path) -> None:
        self.block_connections = False
        self.started = threading.Event()
        self.release = threading.Event()
        super().__init__(path)
        self.block_connections = True

    def _connect(self) -> sqlite3.Connection:
        if self.block_connections:
            self.started.set()
            if not self.release.wait(timeout=3):
                raise TimeoutError("test Security persistence worker release timed out")
        return super()._connect()


class _VisibilityApprovalService(_BlockingApprovalService):
    def __init__(self, path: Path) -> None:
        self.read_entered = threading.Event()
        super().__init__(path)

    def all(self) -> tuple[Any, ...]:
        self.read_entered.set()
        return super().all()


class _BusyApprovalService(SqliteApprovalService):
    def __init__(self, path: Path) -> None:
        self.busy = False
        super().__init__(path)
        self.busy = True

    def _connect(self) -> sqlite3.Connection:
        if self.busy:
            raise sqlite3.OperationalError("database is locked")
        return super()._connect()


class _FailingPersistApprovalService(SqliteApprovalService):
    def __init__(self, path: Path) -> None:
        self.fail_persist = False
        super().__init__(path)
        self.fail_persist = True

    def _persist(self, record: Any) -> None:
        if self.fail_persist:
            raise sqlite3.OperationalError("disk I/O error")
        super()._persist(record)


class _SyncForbiddenApprovals(ApprovalService):
    def get(self, approval_id: str) -> Any:
        del approval_id
        raise AssertionError("sync Approval get must not be used by async Control Plane reads")

    def all(self) -> Any:
        raise AssertionError("sync Approval all must not be used by async Control Plane reads")


class _AsyncOnlyApprovals:
    def __init__(self, record: Any) -> None:
        self.record = record
        self.calls: list[str] = []

    async def get(self, approval_id: str) -> Any:
        self.calls.append("get")
        assert approval_id == self.record.approval_id
        return self.record

    async def all(self) -> tuple[Any, ...]:
        self.calls.append("all")
        return (self.record,)


def _provider() -> LocalAuthorizationProvider:
    return LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="agent:runtime",
                actor_types=frozenset({ActorType.AGENT}),
                approval_actions=frozenset({AuthorizationAction.EXECUTE}),
                resource_types=frozenset({ResourceType.RUN}),
            ),
            LocalPrincipalPolicy(
                principal_ref="user:reviewer",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.APPROVE}),
                resource_types=frozenset({ResourceType.RUN}),
            ),
        )
    )


def _action() -> ProposedAction:
    return ProposedAction(
        AuthorizationContext(
            actor=ActorIdentity("agent:runtime", ActorType.AGENT),
            action=AuthorizationAction.EXECUTE,
            resource_type=ResourceType.RUN,
            resource_id=new_id("run"),
            operation=OperationContext(
                correlation_id="corr-issue-892-security",
                owner_type="agent",
                owner_id="runtime",
            ),
        ),
        payload={"operation": "sensitive"},
    )


def _record() -> Any:
    return ApprovalService().request(
        _action(),
        reason="review required",
        policy_id="policy:test",
    )


def test_security_sqlite_runtime_is_responsive_and_worker_owned(tmp_path: Path) -> None:
    async def scenario() -> None:
        approvals = _RecordingApprovalService(tmp_path / "approvals.sqlite3")
        audit = _RecordingAuditSink(tmp_path / "authorization-audit.sqlite3")
        event_loop_thread = threading.get_ident()
        gate = AuthorizationGate(_provider(), approvals=approvals, audit_sink=audit)

        pending = asyncio.create_task(gate.decide(_action()))
        await asyncio.sleep(0.01)

        assert not pending.done()
        decision = await pending
        assert decision.outcome is AuthorizationOutcome.REQUIRE_APPROVAL
        assert approvals.connection_threads
        assert audit.connection_threads
        assert all(thread_id != event_loop_thread for thread_id in approvals.connection_threads[2:])
        assert all(thread_id != event_loop_thread for thread_id in audit.connection_threads[1:])

    asyncio.run(scenario())


def test_security_persistence_concurrency_is_bounded() -> None:
    async def scenario() -> None:
        offload = SecurityPersistenceOffload(max_concurrency=2)
        counter_lock = threading.Lock()
        active = 0
        max_active = 0

        def operation() -> int:
            nonlocal active, max_active
            with counter_lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.04)
                return threading.get_ident()
            finally:
                with counter_lock:
                    active -= 1

        threads = await asyncio.gather(*(offload.run(operation) for _ in range(8)))
        assert len(set(threads)) <= 2
        assert 1 < max_active <= 2

    asyncio.run(scenario())


def test_security_backlog_does_not_exhaust_default_executor() -> None:
    async def scenario() -> None:
        offload = SecurityPersistenceOffload(max_concurrency=1)
        started = threading.Event()
        release = threading.Event()

        def blocked() -> None:
            started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test Security worker release timed out")

        pending = [asyncio.create_task(offload.run(blocked)) for _ in range(12)]
        assert await asyncio.to_thread(started.wait, 1)

        try:
            default_worker = await asyncio.wait_for(
                asyncio.to_thread(threading.get_ident),
                timeout=0.5,
            )
            assert default_worker != threading.get_ident()
            assert any(not task.done() for task in pending)
        finally:
            release.set()

        await asyncio.gather(*pending)

    asyncio.run(scenario())


def test_authorization_gate_shares_one_security_offload_for_runtime_stores(
    tmp_path: Path,
) -> None:
    approvals = SqliteApprovalService(tmp_path / "approvals.sqlite3")
    audit = SqliteAuthorizationAuditSink(tmp_path / "authorization-audit.sqlite3")
    gate = AuthorizationGate(_provider(), approvals=approvals, audit_sink=audit)

    approval_adapter = cast(AsyncApprovalServiceAdapter, gate.runtime_approvals)
    audit_adapter = cast(AsyncAuthorizationAuditSinkAdapter, gate._runtime_audit_sink)
    assert approval_adapter._offload is audit_adapter._offload


def test_independent_approval_adapters_share_runtime_serialization(tmp_path: Path) -> None:
    approvals = SqliteApprovalService(tmp_path / "approvals.sqlite3")
    first = AsyncApprovalServiceAdapter(approvals)
    second = AsyncApprovalServiceAdapter(approvals)

    assert first.offload is second.offload


def test_multiple_gates_share_runtime_serialization_for_one_approval_service(
    tmp_path: Path,
) -> None:
    approvals = SqliteApprovalService(tmp_path / "approvals.sqlite3")
    first = cast(
        AsyncApprovalServiceAdapter,
        AuthorizationGate(_provider(), approvals=approvals).runtime_approvals,
    )
    second = cast(
        AsyncApprovalServiceAdapter,
        AuthorizationGate(_provider(), approvals=approvals).runtime_approvals,
    )

    assert first.offload is second.offload


def test_shared_approval_runtime_is_weakly_owned(tmp_path: Path) -> None:
    approvals = SqliteApprovalService(tmp_path / "approvals.sqlite3")
    adapter = AsyncApprovalServiceAdapter(approvals)
    approval_ref = weakref.ref(approvals)
    offload_ref = weakref.ref(adapter.offload)

    del adapter
    del approvals
    gc.collect()

    assert approval_ref() is None
    assert offload_ref() is None


def test_gate_and_control_plane_do_not_observe_uncommitted_approval_state(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        approvals = _VisibilityApprovalService(tmp_path / "approvals.sqlite3")
        gate = AuthorizationGate(_provider(), approvals=approvals)
        resources = ApprovalResourceService(approvals)
        action = _action()

        write = asyncio.create_task(
            gate.runtime_approvals.ensure_pending(
                action,
                reason="review required",
                policy_id="policy:test",
            )
        )
        assert await asyncio.to_thread(approvals.started.wait, 1)

        read = asyncio.create_task(
            resources.list_resources(cast(RequestContext, object()), cast(Any, object()))
        )
        await asyncio.sleep(0.02)
        assert not read.done()
        assert not approvals.read_entered.is_set()

        approvals.release.set()
        record, created = await write
        listed = await read

        assert created is True
        assert approvals.read_entered.is_set()
        assert listed[0]["id"] == record.approval_id
        assert SqliteApprovalService(approvals.database_path).get(record.approval_id) == record

    asyncio.run(scenario())


def test_approval_and_audit_serialization_domains_are_independent() -> None:
    async def scenario() -> None:
        offload = SecurityPersistenceOffload(max_concurrency=2)
        approval_started = threading.Event()
        release = threading.Event()

        def blocked_approval() -> None:
            approval_started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test Security approval release timed out")

        approval = asyncio.create_task(offload.run(blocked_approval, serialization="approvals"))
        assert await asyncio.to_thread(approval_started.wait, 1)

        audit_thread = await asyncio.wait_for(
            offload.run(threading.get_ident, serialization="audit"),
            timeout=0.5,
        )
        assert audit_thread != threading.get_ident()
        assert not approval.done()

        release.set()
        await approval

    asyncio.run(scenario())


def test_security_runtime_survives_multiple_event_loop_lifetimes(tmp_path: Path) -> None:
    approvals = SqliteApprovalService(tmp_path / "approvals.sqlite3")
    adapter = AsyncApprovalServiceAdapter(
        approvals,
        offload=SecurityPersistenceOffload(max_concurrency=1),
    )
    action = _action()

    record, created = asyncio.run(
        adapter.ensure_pending(
            action,
            reason="review required",
            policy_id="policy:test",
        )
    )
    assert created is True
    assert asyncio.run(adapter.get(record.approval_id)) == record
    assert asyncio.run(adapter.all()) == (record,)


def test_security_cancellation_waits_for_sqlite_write_boundary(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "approvals.sqlite3"
        approvals = _BlockingApprovalService(database)
        adapter = AsyncApprovalServiceAdapter(approvals)
        action = _action()
        pending = asyncio.create_task(
            adapter.ensure_pending(
                action,
                reason="review required",
                policy_id="policy:test",
            )
        )
        assert await asyncio.to_thread(approvals.started.wait, 1)

        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done()

        approvals.release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending

        restarted = SqliteApprovalService(database)
        persisted = restarted.all()
        assert len(persisted) == 1
        assert persisted[0].requested_action_digest == action.digest

    asyncio.run(scenario())


def test_security_worker_failure_wins_over_pending_cancellation() -> None:
    async def scenario() -> None:
        offload = SecurityPersistenceOffload(max_concurrency=1)
        started = threading.Event()
        release = threading.Event()

        def fail_after_release() -> None:
            started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test Security worker release timed out")
            raise RuntimeError("worker persistence failed")

        pending = asyncio.create_task(offload.run(fail_after_release, serialization="approvals"))
        assert await asyncio.to_thread(started.wait, 1)
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        release.set()

        with pytest.raises(RuntimeError, match="worker persistence failed"):
            await pending

    asyncio.run(scenario())


def test_security_sqlite_busy_maps_to_retryable_transient_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        approvals = _BusyApprovalService(tmp_path / "approvals.sqlite3")
        adapter = AsyncApprovalServiceAdapter(approvals)

        with pytest.raises(ContractError) as raised:
            await adapter.ensure_pending(
                _action(),
                reason="review required",
                policy_id="policy:test",
            )

        assert raised.value.code is ErrorCode.TRANSIENT_FAILURE
        assert raised.value.retryable is True

    asyncio.run(scenario())


def test_failed_approval_write_rolls_back_in_memory_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "approvals.sqlite3"
        approvals = _FailingPersistApprovalService(database)
        adapter = AsyncApprovalServiceAdapter(approvals)

        with pytest.raises(ContractError) as raised:
            await adapter.ensure_pending(
                _action(),
                reason="review required",
                policy_id="policy:test",
            )

        assert raised.value.code is ErrorCode.BACKEND_ERROR
        approvals.fail_persist = False
        assert approvals.all() == ()
        assert SqliteApprovalService(database).all() == ()

    asyncio.run(scenario())


def test_expiration_read_is_persisted_through_async_boundary(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "approvals.sqlite3"
        approvals = SqliteApprovalService(database)
        adapter = AsyncApprovalServiceAdapter(approvals)
        action = _action()
        record, _ = await adapter.ensure_pending(
            action,
            reason="review required",
            policy_id="policy:test",
            expires_at=datetime.now(UTC) + timedelta(milliseconds=150),
        )
        await asyncio.sleep(0.2)

        expired = await adapter.get(record.approval_id)
        assert expired.status is ApprovalStatus.EXPIRED
        restarted = SqliteApprovalService(database)
        assert restarted.get(record.approval_id).status is ApprovalStatus.EXPIRED

    asyncio.run(scenario())


def test_control_plane_approval_reads_use_awaitable_runtime_path() -> None:
    async def scenario() -> None:
        record = _record()
        runtime = _AsyncOnlyApprovals(record)
        resources = ApprovalResourceService(
            _SyncForbiddenApprovals(),
            runtime_approvals=cast(Any, runtime),
        )
        context = cast(RequestContext, object())

        listed = await resources.list_resources(context, cast(Any, object()))
        fetched = await resources.get_resource(context, record.approval_id)

        assert listed[0]["id"] == record.approval_id
        assert fetched["id"] == record.approval_id
        assert runtime.calls == ["all", "get"]

    asyncio.run(scenario())
