"""Brute-force and replay protections for authentication flows."""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta


class InMemoryFailureRateLimiter:
    """Reference brute-force hook; production deployments may replace it."""

    def __init__(self, *, max_failures: int = 5, window: timedelta = timedelta(minutes=5)) -> None:
        if max_failures < 1 or window <= timedelta(0):
            raise ValueError("rate limiter requires positive limits")
        self._max_failures = max_failures
        self._window = window
        self._failures: defaultdict[str, deque[datetime]] = defaultdict(deque)

    def allow(self, key: str, *, now: datetime) -> bool:
        failures = self._failures[key]
        self._prune(failures, now)
        return len(failures) < self._max_failures

    def record(self, key: str, *, success: bool, now: datetime) -> None:
        failures = self._failures[key]
        self._prune(failures, now)
        if success:
            failures.clear()
        else:
            failures.append(now)

    def _prune(self, failures: deque[datetime], now: datetime) -> None:
        cutoff = now - self._window
        while failures and failures[0] <= cutoff:
            failures.popleft()


class InMemoryReplayProtector:
    """Reference request nonce tracker for worker credentials."""

    def __init__(self, *, max_clock_skew: timedelta = timedelta(minutes=5)) -> None:
        if max_clock_skew <= timedelta(0):
            raise ValueError("max_clock_skew must be positive")
        self._max_clock_skew = max_clock_skew
        self._seen: dict[tuple[str, str], datetime] = {}

    def accept(
        self,
        credential_id: str,
        nonce: str,
        issued_at: datetime,
        *,
        now: datetime,
    ) -> bool:
        if not nonce.strip() or abs(now - issued_at) > self._max_clock_skew:
            return False
        self._prune(now)
        key = (credential_id, nonce)
        if key in self._seen:
            return False
        self._seen[key] = max(now, issued_at) + self._max_clock_skew
        return True

    def _prune(self, now: datetime) -> None:
        stale = [key for key, expires_at in self._seen.items() if expires_at <= now]
        for key in stale:
            del self._seen[key]


__all__ = ["InMemoryFailureRateLimiter", "InMemoryReplayProtector"]
