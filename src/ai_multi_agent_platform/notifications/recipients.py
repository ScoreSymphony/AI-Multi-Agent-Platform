"""Recipient-resolution and eligibility contracts for canonical notifications."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts import PlatformEvent

from .models import NotificationCandidate, RecipientRef, RecipientType


class RecipientResolver(Protocol):
    """Resolve canonical recipients without provider-native account identifiers."""

    async def resolve(self, event: PlatformEvent) -> tuple[RecipientRef, ...]: ...


class RecipientEligibilityGuard(Protocol):
    """Check current canonical authorization/membership eligibility before new attention.

    #75 intentionally does not own membership or account lifecycle. #15/#87 adapters can supply
    this boundary so suspended, removed, or otherwise unauthorized recipients stop receiving new
    notifications while historical source events and already-created notifications remain intact.
    """

    async def allows(self, candidate: NotificationCandidate) -> bool: ...


class AllowAllRecipientEligibilityGuard:
    """Default guard for deployments without a membership-aware eligibility provider."""

    async def allows(self, candidate: NotificationCandidate) -> bool:
        del candidate
        return True


class StaticRecipientEligibilityGuard:
    """Deterministic fixture for authorization/membership integration tests."""

    def __init__(self, *, denied: tuple[RecipientRef, ...] = ()) -> None:
        self._denied = frozenset(denied)

    async def allows(self, candidate: NotificationCandidate) -> bool:
        return candidate.recipient not in self._denied


class EventOwnerRecipientResolver:
    """Resolve the canonical owner attached to a platform event when it is user-facing."""

    async def resolve(self, event: PlatformEvent) -> tuple[RecipientRef, ...]:
        owner = event.owner_ref
        if owner is None:
            return ()
        try:
            recipient_type = RecipientType(owner.type)
        except ValueError:
            return ()
        return (RecipientRef(type=recipient_type, id=owner.id),)


class StaticRecipientResolver:
    """Deterministic fixture for policies that already resolved recipients elsewhere."""

    def __init__(self, recipients: tuple[RecipientRef, ...]) -> None:
        self._recipients = tuple(dict.fromkeys(recipients))

    async def resolve(self, event: PlatformEvent) -> tuple[RecipientRef, ...]:
        del event
        return self._recipients
