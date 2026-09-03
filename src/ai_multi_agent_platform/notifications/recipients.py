"""Recipient-resolution contracts for canonical notifications."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts import PlatformEvent

from .models import RecipientRef, RecipientType


class RecipientResolver(Protocol):
    """Resolve canonical recipients without provider-native account identifiers."""

    async def resolve(self, event: PlatformEvent) -> tuple[RecipientRef, ...]: ...


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
