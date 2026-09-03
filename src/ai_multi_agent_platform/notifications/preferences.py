"""Notification preference storage and filtering."""

from __future__ import annotations

from threading import RLock
from typing import Protocol

from .models import (
    NotificationCandidate,
    NotificationPreference,
    NotificationSeverity,
    RecipientRef,
)


_SEVERITY_ORDER = {
    NotificationSeverity.INFO: 0,
    NotificationSeverity.WARNING: 1,
    NotificationSeverity.ERROR: 2,
    NotificationSeverity.CRITICAL: 3,
}


class NotificationPreferenceRepository(Protocol):
    def get(self, recipient: RecipientRef) -> NotificationPreference: ...

    def save(self, preference: NotificationPreference) -> NotificationPreference: ...


class InMemoryNotificationPreferenceRepository:
    def __init__(self) -> None:
        self._items: dict[RecipientRef, NotificationPreference] = {}
        self._lock = RLock()

    def get(self, recipient: RecipientRef) -> NotificationPreference:
        with self._lock:
            return self._items.get(recipient, NotificationPreference(recipient=recipient))

    def save(self, preference: NotificationPreference) -> NotificationPreference:
        with self._lock:
            self._items[preference.recipient] = preference
            return preference


def preference_allows(
    preference: NotificationPreference,
    candidate: NotificationCandidate,
) -> bool:
    """Return whether the attention rule is enabled, independent of delivery channel."""

    if preference.muted:
        return False
    if candidate.category not in preference.enabled_categories:
        return False
    if _SEVERITY_ORDER[candidate.severity] < _SEVERITY_ORDER[preference.minimum_severity]:
        return False
    if preference.project_ids:
        return candidate.project_id is not None and candidate.project_id in preference.project_ids
    return True
