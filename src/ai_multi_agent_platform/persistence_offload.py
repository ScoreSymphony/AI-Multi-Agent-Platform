"""Shared lifetime-safe identity registry for dedicated runtime persistence offloads."""

from __future__ import annotations

import threading
import weakref
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class _SharedOffloadEntry[_OffloadT]:
    offload: _OffloadT
    owners: list[weakref.ReferenceType[object]]


class SharedPersistenceOffloadRegistry[_OffloadT]:
    """Share one offload per live repository identity without retaining repositories.

    Repository protocols do not require hashability or weak-reference support. The registry
    therefore keys entries by ``id(repository)`` and tracks adapter owners weakly instead of
    weak-referencing the repository itself. Every live adapter owns the repository strongly, so
    an identity cannot be reused while an entry has a live owner. Once the final adapter is
    collected, the entry and its offload are released as well.
    """

    def __init__(self) -> None:
        self._entries: dict[int, _SharedOffloadEntry[_OffloadT]] = {}
        self._lock = threading.Lock()

    def resolve(
        self,
        repository: object,
        *,
        owner: object,
        requested: _OffloadT | None,
        factory: Callable[[], _OffloadT],
    ) -> _OffloadT:
        repository_id = id(repository)
        with self._lock:
            entry = self._entries.get(repository_id)
            if entry is not None:
                entry.owners = [reference for reference in entry.owners if reference() is not None]
                if entry.owners:
                    entry.owners.append(self._owner_reference(repository_id, owner))
                    return entry.offload
                self._entries.pop(repository_id, None)

            resolved = requested or factory()
            self._entries[repository_id] = _SharedOffloadEntry(
                offload=resolved,
                owners=[self._owner_reference(repository_id, owner)],
            )
            return resolved

    def _owner_reference(
        self,
        repository_id: int,
        owner: object,
    ) -> weakref.ReferenceType[object]:
        return weakref.ref(
            owner,
            lambda _reference: self._release_owner(repository_id),
        )

    def _release_owner(self, repository_id: int) -> None:
        with self._lock:
            entry = self._entries.get(repository_id)
            if entry is None:
                return
            entry.owners = [reference for reference in entry.owners if reference() is not None]
            if not entry.owners:
                self._entries.pop(repository_id, None)


__all__ = ["SharedPersistenceOffloadRegistry"]
