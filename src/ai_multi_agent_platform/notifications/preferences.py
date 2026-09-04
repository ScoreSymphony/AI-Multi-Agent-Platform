"""Notification preference storage and filtering."""

from __future__ import annotations

from datetime import datetime, time
from threading import RLock
from typing import Protocol
from zoneinfo import ZoneInfo

from .models import (
    NotificationCandidate,
    NotificationCategory,
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
        if candidate.project_id is None or candidate.project_id not in preference.project_ids:
            return False
    if candidate.category is NotificationCategory.DEADLINE:
        phase = candidate.summary.get("phase")
        if phase == "approaching" and not preference.deadline_reminders_enabled:
            return False
        if phase == "overdue" and not preference.overdue_reminders_enabled:
            return False
    return True


def external_delivery_allowed(preference: NotificationPreference, *, now: datetime) -> bool:
    """Apply quiet hours only to external delivery; the canonical in-app inbox is unaffected."""

    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if preference.quiet_hours_start is None:
        return True
    if preference.quiet_hours_end is None or preference.quiet_hours_timezone is None:
        raise ValueError("quiet-hours preference is incomplete")

    local_time = now.astimezone(ZoneInfo(preference.quiet_hours_timezone)).time().replace(tzinfo=None)
    start = time.fromisoformat(preference.quiet_hours_start)
    end = time.fromisoformat(preference.quiet_hours_end)
    if start < end:
        quiet = start <= local_time < end
    else:
        quiet = local_time >= start or local_time < end
    return not quiet
