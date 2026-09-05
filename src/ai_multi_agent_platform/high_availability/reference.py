"""Deterministic reference coordination provider for Control Plane HA tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from .contracts import (
    CoordinationLease,
    CoordinationState,
    CoordinationUnavailable,
    FencingToken,
    LeadershipConflict,
    StaleFencingToken,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


Clock = Callable[[], datetime]


class InMemoryCoordinationProvider:
    """Process-local deterministic lease authority used by contract/integration fixtures.

    This implementation intentionally does not pretend to provide multi-host durability. Multiple
    Control Plane service objects can share one instance to prove promotion and fencing semantics.
    Production HA adapters must implement the same contract using a backend whose consistency and
    availability characteristics match the selected deployment profile.
    """

    def __init__(self, *, clock: Clock = utc_now) -> None:
        self._clock = clock
        self._lock = asyncio.Lock()
        self._available = True
        self._epoch = 0
        self._owner_instance_id: str | None = None
        self._acquired_at: datetime | None = None
        self._expires_at: datetime | None = None

    def set_available(self, available: bool) -> None:
        """Deterministically simulate coordination outage/recovery."""

        self._available = available

    async def acquire(self, instance_id: str, *, ttl: timedelta) -> CoordinationLease:
        self._validate_instance_id(instance_id)
        self._validate_ttl(ttl)
        self._ensure_available()
        async with self._lock:
            self._ensure_available()
            now = self._clock()
            self._expire_if_needed(now)
            if self._owner_instance_id is not None:
                if self._owner_instance_id == instance_id:
                    return self._current_lease()
                raise LeadershipConflict(
                    "another Control Plane instance owns the non-expired leadership lease"
                )

            self._epoch += 1
            self._owner_instance_id = instance_id
            self._acquired_at = now
            self._expires_at = now + ttl
            return self._current_lease()

    async def renew(self, token: FencingToken, *, ttl: timedelta) -> CoordinationLease:
        self._validate_ttl(ttl)
        self._ensure_available()
        async with self._lock:
            self._ensure_available()
            now = self._clock()
            self._expire_if_needed(now)
            self._require_current(token)
            self._expires_at = now + ttl
            return self._current_lease()

    async def release(self, token: FencingToken) -> None:
        self._ensure_available()
        async with self._lock:
            self._ensure_available()
            now = self._clock()
            self._expire_if_needed(now)
            self._require_current(token)
            self._clear_owner()

    async def inspect(self) -> CoordinationState:
        self._ensure_available()
        async with self._lock:
            self._ensure_available()
            now = self._clock()
            self._expire_if_needed(now)
            return CoordinationState(
                epoch=self._epoch,
                owner_instance_id=self._owner_instance_id,
                expires_at=self._expires_at,
            )

    async def assert_fence(self, token: FencingToken) -> None:
        self._ensure_available()
        async with self._lock:
            self._ensure_available()
            now = self._clock()
            self._expire_if_needed(now)
            self._require_current(token)

    def _ensure_available(self) -> None:
        if not self._available:
            raise CoordinationUnavailable("coordination backend is unavailable")

    @staticmethod
    def _validate_instance_id(instance_id: str) -> None:
        if not instance_id.strip():
            raise ValueError("instance_id must not be blank")

    @staticmethod
    def _validate_ttl(ttl: timedelta) -> None:
        if ttl <= timedelta(0):
            raise ValueError("lease ttl must be positive")

    def _expire_if_needed(self, now: datetime) -> None:
        if self._expires_at is not None and now >= self._expires_at:
            self._clear_owner()

    def _require_current(self, token: FencingToken) -> None:
        if self._owner_instance_id != token.instance_id or self._epoch != token.epoch:
            raise StaleFencingToken(
                "leadership fencing token is stale or belongs to another Control Plane instance"
            )

    def _current_lease(self) -> CoordinationLease:
        if self._owner_instance_id is None or self._acquired_at is None or self._expires_at is None:
            raise RuntimeError("coordination provider has no active lease")
        return CoordinationLease(
            token=FencingToken(
                instance_id=self._owner_instance_id,
                epoch=self._epoch,
            ),
            acquired_at=self._acquired_at,
            expires_at=self._expires_at,
        )

    def _clear_owner(self) -> None:
        self._owner_instance_id = None
        self._acquired_at = None
        self._expires_at = None
