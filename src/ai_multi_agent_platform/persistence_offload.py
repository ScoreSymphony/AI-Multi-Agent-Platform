"""Shared lifetime-safe identity registry for dedicated runtime persistence offloads."""

from __future__ import annotations

import threading
import weakref
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class _SharedOffloadEntry[OffloadT]:
    offload: OffloadT
    owners: list[weakref.ReferenceType[object]]


class SharedPersistenceOffloadRegistry[OffloadT]:
    """Share one offload per live repository identity without retaining repositories.

    Repository protocols do not require hashability or weak-reference support. The registry
    therefore keys entries by ``id(repository)`` and tracks adapter owners weakly instead of
    weak-referencing the repository itself. Every live adapter owns the repository strongly, so
    an identity cannot be reused while an entry has a live owner. Once the final adapter is
    collected, the entry and its offload are released as well.

    Weakref callbacks can run synchronously while ``resolve`` already holds the registry lock,
    for example when allocation or a factory call makes an otherwise unreachable owner collectible.
    The lock is therefore reentrant. Callbacks also remove only their own weakref by identity and
    never dereference sibling owners while the registry lock is held.
    """

    def __init__(self) -> None:
        self._entries: dict[int, _SharedOffloadEntry[OffloadT]] = {}
        self._lock = threading.RLock()

    def resolve(
        self,
        repository: object,
        *,
        owner: object,
        requested: OffloadT | None,
        factory: Callable[[], OffloadT],
    ) -> OffloadT:
        repository_id = id(repository)
        with self._lock:
            entry = self._entries.get(repository_id)
            if entry is not None:
                entry.owners.append(self._owner_reference(repository_id, owner))
                return entry.offload

            resolved = requested if requested is not None else factory()
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
            lambda reference: self._release_owner(repository_id, reference),
        )

    def _release_owner(
        self,
        repository_id: int,
        released_reference: weakref.ReferenceType[object],
    ) -> None:
        with self._lock:
            entry = self._entries.get(repository_id)
            if entry is None:
                return
            entry.owners = [
                reference for reference in entry.owners if reference is not released_reference
            ]
            if not entry.owners:
                self._entries.pop(repository_id, None)


__all__ = ["SharedPersistenceOffloadRegistry"]
