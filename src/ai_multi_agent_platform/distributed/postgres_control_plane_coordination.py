"""PostgreSQL-backed coordination for optional multi-instance Control Plane HA."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Protocol, Self, TypeVar, cast

from ai_multi_agent_platform.high_availability.contracts import (
    CoordinationLease,
    CoordinationState,
    CoordinationUnavailable,
    FencingToken,
    LeadershipConflict,
    StaleFencingToken,
)

_DEFAULT_LEASE_NAME = "control-plane"
_T = TypeVar("_T")


class _Cursor(Protocol):
    def execute(self, query: str, params: Sequence[object] | None = None) -> Self: ...

    def fetchone(self) -> Sequence[object] | None: ...

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[str], _Connection]


class PostgresCoordinationProvider:
    """Self-hostable PostgreSQL implementation of the existing HA coordination contract.

    PostgreSQL is an optional adapter, not canonical HA architecture. Every authority decision is
    serialized by a transactional row lock and uses PostgreSQL's server clock for lease expiry.
    The DSN is retained only as private adapter configuration and is never included in canonical
    state or translated error messages.
    """

    def __init__(
        self,
        dsn: str,
        *,
        lease_name: str = _DEFAULT_LEASE_NAME,
        connect: ConnectionFactory | None = None,
    ) -> None:
        if not dsn.strip():
            raise ValueError("PostgreSQL coordination DSN must not be blank")
        if not lease_name.strip():
            raise ValueError("lease_name must not be blank")
        self._dsn = dsn
        self._lease_name = lease_name
        self._connect = connect or _load_psycopg_connect()

    async def initialize(self) -> None:
        """Create the adapter-owned coordination table and singleton lease row.

        Production deployments may run this once with a bootstrap/migration identity and then use a
        narrower runtime database identity for ordinary coordination operations.
        """

        await self._run(self._initialize_schema)

    async def acquire(self, instance_id: str, *, ttl: timedelta) -> CoordinationLease:
        _validate_instance_id(instance_id)
        ttl_seconds = _ttl_seconds(ttl)
        return await self._run(
            lambda connection: self._acquire(connection, instance_id, ttl_seconds)
        )

    async def renew(self, token: FencingToken, *, ttl: timedelta) -> CoordinationLease:
        ttl_seconds = _ttl_seconds(ttl)
        return await self._run(lambda connection: self._renew(connection, token, ttl_seconds))

    async def release(self, token: FencingToken) -> None:
        await self._run(lambda connection: self._release(connection, token))

    async def inspect(self) -> CoordinationState:
        return await self._run(self._inspect)

    async def assert_fence(self, token: FencingToken) -> None:
        await self._run(lambda connection: self._assert_fence(connection, token))

    async def _run(self, operation: Callable[[_Connection], _T]) -> _T:
        worker = asyncio.create_task(asyncio.to_thread(self._run_sync, operation))
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
                if isinstance(failure, (LeadershipConflict, StaleFencingToken)):
                    raise failure
                if isinstance(failure, CoordinationUnavailable):
                    raise failure
                raise CoordinationUnavailable(
                    "PostgreSQL coordination backend is unavailable"
                ) from None
            raise
        except (LeadershipConflict, StaleFencingToken):
            raise
        except CoordinationUnavailable:
            raise
        except Exception:
            raise CoordinationUnavailable(
                "PostgreSQL coordination backend is unavailable"
            ) from None

    def _run_sync(self, operation: Callable[[_Connection], _T]) -> _T:
        connection = self._connect(self._dsn)
        try:
            result = operation(connection)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_schema(self, connection: _Connection) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_map_control_plane_coordination (
                    lease_name TEXT PRIMARY KEY,
                    epoch BIGINT NOT NULL CHECK (epoch >= 0),
                    owner_instance_id TEXT NULL,
                    acquired_at TIMESTAMPTZ NULL,
                    expires_at TIMESTAMPTZ NULL,
                    CHECK (
                        (owner_instance_id IS NULL
                         AND acquired_at IS NULL
                         AND expires_at IS NULL)
                        OR
                        (owner_instance_id IS NOT NULL
                         AND acquired_at IS NOT NULL
                         AND expires_at IS NOT NULL)
                    )
                )
                """
            )
            cursor.execute(
                """
                INSERT INTO ai_map_control_plane_coordination (
                    lease_name, epoch, owner_instance_id, acquired_at, expires_at
                ) VALUES (%s, 0, NULL, NULL, NULL)
                ON CONFLICT (lease_name) DO NOTHING
                """,
                (self._lease_name,),
            )

    def _locked_state(
        self, connection: _Connection
    ) -> tuple[int, str | None, datetime | None, datetime | None, datetime]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT epoch, owner_instance_id, acquired_at, expires_at, clock_timestamp()
                FROM ai_map_control_plane_coordination
                WHERE lease_name = %s
                FOR UPDATE
                """,
                (self._lease_name,),
            )
            return _decode_state_row(cursor.fetchone())

    def _acquire(
        self,
        connection: _Connection,
        instance_id: str,
        ttl_seconds: float,
    ) -> CoordinationLease:
        epoch, owner, acquired_at, expires_at, now = self._locked_state(connection)
        if owner is not None and expires_at is not None and now < expires_at:
            if owner != instance_id:
                raise LeadershipConflict(
                    "another Control Plane instance owns the non-expired leadership lease"
                )
            if acquired_at is None:
                raise CoordinationUnavailable("PostgreSQL coordination state is inconsistent")
            return _lease(owner, epoch, acquired_at, expires_at)

        new_epoch = epoch + 1
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_map_control_plane_coordination
                SET epoch = %s,
                    owner_instance_id = %s,
                    acquired_at = clock_timestamp(),
                    expires_at = clock_timestamp() + (%s * INTERVAL '1 second')
                WHERE lease_name = %s
                RETURNING epoch, owner_instance_id, acquired_at, expires_at
                """,
                (new_epoch, instance_id, ttl_seconds, self._lease_name),
            )
            return _decode_lease_row(cursor.fetchone())

    def _renew(
        self,
        connection: _Connection,
        token: FencingToken,
        ttl_seconds: float,
    ) -> CoordinationLease:
        epoch, owner, acquired_at, expires_at, now = self._locked_state(connection)
        _require_current(token, epoch, owner, expires_at, now)
        if acquired_at is None:
            raise CoordinationUnavailable("PostgreSQL coordination state is inconsistent")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_map_control_plane_coordination
                SET expires_at = clock_timestamp() + (%s * INTERVAL '1 second')
                WHERE lease_name = %s
                RETURNING epoch, owner_instance_id, acquired_at, expires_at
                """,
                (ttl_seconds, self._lease_name),
            )
            return _decode_lease_row(cursor.fetchone())

    def _release(self, connection: _Connection, token: FencingToken) -> None:
        epoch, owner, _acquired_at, expires_at, now = self._locked_state(connection)
        _require_current(token, epoch, owner, expires_at, now)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ai_map_control_plane_coordination
                SET owner_instance_id = NULL,
                    acquired_at = NULL,
                    expires_at = NULL
                WHERE lease_name = %s
                """,
                (self._lease_name,),
            )

    def _inspect(self, connection: _Connection) -> CoordinationState:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT epoch, owner_instance_id, acquired_at, expires_at, clock_timestamp()
                FROM ai_map_control_plane_coordination
                WHERE lease_name = %s
                """,
                (self._lease_name,),
            )
            epoch, owner, _acquired_at, expires_at, now = _decode_state_row(cursor.fetchone())
        if owner is not None and expires_at is not None and now >= expires_at:
            owner = None
            expires_at = None
        return CoordinationState(
            epoch=epoch,
            owner_instance_id=owner,
            expires_at=expires_at,
            available=True,
        )

    def _assert_fence(self, connection: _Connection, token: FencingToken) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT epoch, owner_instance_id, acquired_at, expires_at, clock_timestamp()
                FROM ai_map_control_plane_coordination
                WHERE lease_name = %s
                """,
                (self._lease_name,),
            )
            epoch, owner, _acquired_at, expires_at, now = _decode_state_row(cursor.fetchone())
        _require_current(token, epoch, owner, expires_at, now)


def _load_psycopg_connect() -> ConnectionFactory:
    try:
        module = importlib.import_module("psycopg")
    except ImportError:
        raise RuntimeError(
            "PostgreSQL HA coordination requires the optional 'ha-postgres' dependency"
        ) from None
    return cast(ConnectionFactory, module.connect)


def _validate_instance_id(instance_id: str) -> None:
    if not instance_id.strip():
        raise ValueError("instance_id must not be blank")


def _ttl_seconds(ttl: timedelta) -> float:
    if ttl <= timedelta(0):
        raise ValueError("lease ttl must be positive")
    return ttl.total_seconds()


def _decode_state_row(
    row: Sequence[object] | None,
) -> tuple[int, str | None, datetime | None, datetime | None, datetime]:
    if row is None or len(row) != 5:
        raise CoordinationUnavailable("PostgreSQL coordination state is missing")
    epoch = _as_int(row[0])
    owner = _as_optional_str(row[1])
    acquired_at = _as_optional_datetime(row[2])
    expires_at = _as_optional_datetime(row[3])
    now = _as_datetime(row[4])
    return epoch, owner, acquired_at, expires_at, now


def _decode_lease_row(row: Sequence[object] | None) -> CoordinationLease:
    if row is None or len(row) != 4:
        raise CoordinationUnavailable("PostgreSQL coordination lease update returned no state")
    epoch = _as_int(row[0])
    owner = _as_optional_str(row[1])
    acquired_at = _as_optional_datetime(row[2])
    expires_at = _as_optional_datetime(row[3])
    if owner is None or acquired_at is None or expires_at is None:
        raise CoordinationUnavailable("PostgreSQL coordination lease state is incomplete")
    return _lease(owner, epoch, acquired_at, expires_at)


def _lease(
    owner: str, epoch: int, acquired_at: datetime, expires_at: datetime
) -> CoordinationLease:
    return CoordinationLease(
        token=FencingToken(instance_id=owner, epoch=epoch),
        acquired_at=acquired_at,
        expires_at=expires_at,
    )


def _require_current(
    token: FencingToken,
    epoch: int,
    owner: str | None,
    expires_at: datetime | None,
    now: datetime,
) -> None:
    if (
        owner != token.instance_id
        or epoch != token.epoch
        or expires_at is None
        or now >= expires_at
    ):
        raise StaleFencingToken(
            "leadership fencing token is stale or belongs to another Control Plane instance"
        )


def _as_int(value: object) -> int:
    if not isinstance(value, int):
        raise CoordinationUnavailable("PostgreSQL coordination epoch has invalid type")
    return value


def _as_optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CoordinationUnavailable("PostgreSQL coordination owner has invalid type")
    return value


def _as_datetime(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CoordinationUnavailable("PostgreSQL coordination timestamp has invalid type")
    return value


def _as_optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    return _as_datetime(value)
