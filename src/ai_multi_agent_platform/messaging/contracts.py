from __future__ import annotations

from abc import abstractmethod
from collections.abc import AsyncIterator
from typing import Protocol

from ai_multi_agent_platform.contracts import OperationControl, ProviderContract

from .models import DeadLetter, MessageDelivery, PublishReceipt, Subscription, TransportEnvelope


class MessageSubscription(Protocol):
    """Cancelable async delivery stream returned by a transport subscription."""

    def __aiter__(self) -> AsyncIterator[MessageDelivery]: ...

    async def __anext__(self) -> MessageDelivery: ...

    async def aclose(self) -> None: ...


class MessageTransport(ProviderContract):
    """Replaceable delivery boundary for commands, events and signals.

    A transport is not the canonical event store. Its baseline guarantee is
    at-least-once delivery, so consumers must tolerate duplicate deliveries.
    """

    @abstractmethod
    async def publish(
        self,
        topic: str,
        envelope: TransportEnvelope,
        *,
        control: OperationControl | None = None,
    ) -> PublishReceipt:
        """Publish one transport envelope.

        ``OperationControl.timeout_seconds`` bounds the publish operation when
        supported by the adapter. When ``OperationControl.idempotency_key`` is
        supplied it must refer to the same key carried by the envelope; it does
        not create a separate exactly-once identity. ``retry_mode`` expresses
        caller retry intent and must not cause hidden exactly-once claims.
        """
        ...

    @abstractmethod
    def subscribe(self, subscription: Subscription) -> MessageSubscription: ...

    @abstractmethod
    async def ack(self, delivery: MessageDelivery) -> None: ...

    @abstractmethod
    async def nack(
        self,
        delivery: MessageDelivery,
        *,
        retry: bool = True,
        reason: str | None = None,
    ) -> None: ...

    @abstractmethod
    async def dead_letters(
        self,
        topic: str,
        consumer_group: str,
    ) -> tuple[DeadLetter, ...]: ...

    @abstractmethod
    async def close(self, *, graceful: bool = True) -> None:
        """Stop accepting new messages.

        Graceful close allows consumers to drain or reconnect for retained
        messages. Forced close abandons pending deliveries in this transport;
        it never mutates canonical domain/event history.
        """
        ...
