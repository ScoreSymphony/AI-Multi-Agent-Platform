"""Authorization-aware visibility boundary for canonical notifications."""

from __future__ import annotations

from typing import Protocol

from .models import Notification, RecipientRef


class NotificationVisibilityGuard(Protocol):
    """Decide whether a recipient may currently observe a projected notification.

    Notification persistence is historical attention state. Visibility is deliberately
    re-evaluated at read time so a later authorization or membership change cannot leak the
    existence of a source resource through inbox items, unread counts, or notification actions.
    """

    async def allows(self, notification: Notification, *, recipient: RecipientRef) -> bool: ...


class AllowAllNotificationVisibilityGuard:
    """Reference fallback for embeddings that do not provide a source-visibility resolver."""

    async def allows(self, notification: Notification, *, recipient: RecipientRef) -> bool:
        del notification, recipient
        return True


__all__ = ["AllowAllNotificationVisibilityGuard", "NotificationVisibilityGuard"]
