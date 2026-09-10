"""Reference application-release repository."""

from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .models import ApplicationRelease


class InMemoryApplicationReleaseRepository:
    """Deterministic optimistic-concurrency repository for tests and composition."""

    def __init__(self) -> None:
        self._items: dict[str, ApplicationRelease] = {}
        self._lock = asyncio.Lock()

    async def get(self, release_id: str) -> ApplicationRelease:
        try:
            return self._items[release_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"application release not found: {release_id}") from exc

    async def list(self) -> tuple[ApplicationRelease, ...]:
        return tuple(sorted(self._items.values(), key=lambda item: item.created_at))

    async def find_version(
        self,
        application_id: str,
        version: str,
        channel: str,
    ) -> ApplicationRelease | None:
        for release in self._items.values():
            if (
                release.application_id == application_id
                and release.version == version
                and release.channel.value == channel
            ):
                return release
        return None

    async def save(
        self,
        release: ApplicationRelease,
        *,
        expected_revision: int | None,
    ) -> ApplicationRelease:
        async with self._lock:
            current = self._items.get(release.release_id)
            if current is None:
                if expected_revision not in {None, 0}:
                    raise ContractError(ErrorCode.CONFLICT, "application release revision conflict")
            else:
                if expected_revision is None or current.revision != expected_revision:
                    raise ContractError(ErrorCode.CONFLICT, "application release revision conflict")
            self._items[release.release_id] = release
            return release
