from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.distributed.postgres_control_plane_coordination import (
    PostgresCoordinationProvider,
)
from ai_multi_agent_platform.high_availability import (
    CoordinationUnavailable,
    FencingToken,
    LeadershipConflict,
    StaleFencingToken,
)

TTL = timedelta(seconds=10)
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


@dataclass
class _State:
    epoch: int = 0
    owner: str | None = None
    acquired_at: datetime | None = None
    expires_at: datetime | None = None


class _FakePostgres:
    def __init__(self) -> None:
        self.now = NOW
        self.state = _State()
        self.lock = threading.Lock()

    def connect(self, _dsn: str) -> _FakeConnection:
        return _FakeConnection(self)

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class _FakeConnection:
    def __init__(self, database: _FakePostgres) -> None:
        self.database = database
        self._locked = False
        self._snapshot: _State | None = None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def lock_row(self) -> None:
        if self._locked:
            return
        self.database.lock.acquire()
        self._locked = True
        state = self.database.state
        self._snapshot = _State(
            epoch=state.epoch,
            owner=state.owner,
            acquired_at=state.acquired_at,
            expires_at=state.expires_at,
        )

    def commit(self) -> None:
        self._finish()

    def rollback(self) -> None:
        if self._locked and self._snapshot is not None:
            self.database.state = self._snapshot
        self._finish()

    def close(self) -> None:
        self._finish()

    def _finish(self) -> None:
        if self._locked:
            self._locked = False
            self.database.lock.release()
        self._snapshot = None


class _FakeCursor:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection
        self._row: tuple[object, ...] | None = None

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def execute(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> _FakeCursor:
        sql = " ".join(query.split())
        database = self.connection.database
        state = database.state

        if sql.startswith("CREATE TABLE") or sql.startswith("INSERT INTO"):
            return self

        if sql.startswith("SELECT"):
            if "FOR UPDATE" in sql:
                self.connection.lock_row()
                state = database.state
                self._row = (
                    state.epoch,
                    state.owner,
                    state.acquired_at,
                    state.expires_at,
                    database.now,
                )
            else:
                with database.lock:
                    state = database.state
                    self._row = (
                        state.epoch,
                        state.owner,
                        state.acquired_at,
                        state.expires_at,
                        database.now,
                    )
            return self

        if "SET epoch = %s" in sql:
            assert params is not None
            new_epoch, owner, ttl_seconds, _lease_name = params
            assert isinstance(new_epoch, int)
            assert isinstance(owner, str)
            assert isinstance(ttl_seconds, float)
            database.state = _State(
                epoch=new_epoch,
                owner=owner,
                acquired_at=database.now,
                expires_at=database.now + timedelta(seconds=ttl_seconds),
            )
            state = database.state
            self._row = (state.epoch, state.owner, state.acquired_at, state.expires_at)
            return self

        if "SET expires_at = clock_timestamp()" in sql:
            assert params is not None
            ttl_seconds, _lease_name = params
            assert isinstance(ttl_seconds, float)
            state.expires_at = database.now + timedelta(seconds=ttl_seconds)
            self._row = (state.epoch, state.owner, state.acquired_at, state.expires_at)
            return self

        if "SET owner_instance_id = NULL" in sql:
            state.owner = None
            state.acquired_at = None
            state.expires_at = None
            return self

        raise AssertionError(f"unexpected SQL in deterministic fixture: {sql}")

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row


def test_postgres_provider_serializes_acquisition_and_fences_expired_leader() -> None:
    async def scenario() -> None:
        database = _FakePostgres()
        first = PostgresCoordinationProvider("secret-dsn", connect=database.connect)
        second = PostgresCoordinationProvider("secret-dsn", connect=database.connect)
        await first.initialize()

        results = await asyncio.gather(
            first.acquire("control-a", ttl=TTL),
            second.acquire("control-b", ttl=TTL),
            return_exceptions=True,
        )

        leases = [item for item in results if not isinstance(item, BaseException)]
        conflicts = [item for item in results if isinstance(item, LeadershipConflict)]
        assert len(leases) == 1
        assert len(conflicts) == 1
        winning_lease = leases[0]
        assert winning_lease.token.epoch == 1

        old_owner = winning_lease.token.instance_id
        new_owner = "control-b" if old_owner == "control-a" else "control-a"
        new_provider = second if new_owner == "control-b" else first
        old_provider = first if old_owner == "control-a" else second

        database.advance(TTL + timedelta(milliseconds=1))
        promoted = await new_provider.acquire(new_owner, ttl=TTL)
        assert promoted.token == FencingToken(instance_id=new_owner, epoch=2)

        with pytest.raises(StaleFencingToken):
            await old_provider.assert_fence(winning_lease.token)

        state = await new_provider.inspect()
        assert state.owner_instance_id == new_owner
        assert state.epoch == 2

    asyncio.run(scenario())


def test_postgres_provider_renew_and_release_preserve_monotonic_epoch() -> None:
    async def scenario() -> None:
        database = _FakePostgres()
        provider = PostgresCoordinationProvider("secret-dsn", connect=database.connect)
        await provider.initialize()

        lease = await provider.acquire("control-a", ttl=TTL)
        original_expiry = lease.expires_at
        database.advance(timedelta(seconds=2))
        renewed = await provider.renew(lease.token, ttl=TTL)
        assert renewed.token == lease.token
        assert renewed.expires_at > original_expiry

        await provider.release(renewed.token)
        state = await provider.inspect()
        assert state.owner_instance_id is None
        assert state.epoch == 1

        replacement = await provider.acquire("control-b", ttl=TTL)
        assert replacement.token == FencingToken(instance_id="control-b", epoch=2)

    asyncio.run(scenario())


def test_postgres_provider_uses_backend_expiry_not_local_wall_clock() -> None:
    async def scenario() -> None:
        database = _FakePostgres()
        provider = PostgresCoordinationProvider("secret-dsn", connect=database.connect)
        await provider.initialize()
        lease = await provider.acquire("control-a", ttl=TTL)

        database.advance(TTL + timedelta(seconds=1))
        state = await provider.inspect()
        assert state.owner_instance_id is None
        assert state.epoch == 1
        with pytest.raises(StaleFencingToken):
            await provider.assert_fence(lease.token)

    asyncio.run(scenario())


def test_postgres_provider_maps_backend_failure_without_leaking_dsn() -> None:
    dsn = "postgresql://operator:super-secret@example.invalid/platform"

    def unavailable(_dsn: str) -> _FakeConnection:
        raise OSError(f"cannot connect to {_dsn}")

    async def scenario() -> None:
        provider = PostgresCoordinationProvider(dsn, connect=unavailable)
        with pytest.raises(CoordinationUnavailable) as exc_info:
            await provider.inspect()
        message = str(exc_info.value)
        assert "super-secret" not in message
        assert dsn not in message

    asyncio.run(scenario())


def test_postgres_provider_validates_configuration_before_backend_access() -> None:
    database = _FakePostgres()
    with pytest.raises(ValueError, match="DSN"):
        PostgresCoordinationProvider("", connect=database.connect)
    with pytest.raises(ValueError, match="lease_name"):
        PostgresCoordinationProvider(
            "postgresql://db/platform", lease_name=" ", connect=database.connect
        )

    async def scenario() -> None:
        provider = PostgresCoordinationProvider(
            "postgresql://db/platform", connect=database.connect
        )
        with pytest.raises(ValueError, match="instance_id"):
            await provider.acquire(" ", ttl=TTL)
        with pytest.raises(ValueError, match="positive"):
            await provider.acquire("control-a", ttl=timedelta(0))

    asyncio.run(scenario())


def test_postgres_provider_defers_cancellation_until_transaction_boundary() -> None:
    finished = threading.Event()
    database = _FakePostgres()

    class _SlowConnection(_FakeConnection):
        def cursor(self) -> _FakeCursor:
            parent = self

            class _SlowCursor(_FakeCursor):
                def execute(
                    self,
                    query: str,
                    params: tuple[object, ...] | None = None,
                ) -> _FakeCursor:
                    if query.lstrip().startswith("SELECT"):
                        time.sleep(0.05)
                        finished.set()
                    return super().execute(query, params)

            return _SlowCursor(parent)

    def connect(_dsn: str) -> _FakeConnection:
        return _SlowConnection(database)

    async def scenario() -> None:
        provider = PostgresCoordinationProvider("secret-dsn", connect=connect)
        await provider.initialize()
        task = asyncio.create_task(provider.inspect())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(scenario())
