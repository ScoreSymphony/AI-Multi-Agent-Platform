from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from uuid import uuid4

from ai_multi_agent_platform.contracts import (
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    HealthStatus,
    OperationControl,
    ProviderDescriptor,
)

from .contracts import MessageSubscription, MessageTransport
from .models import (
    DeadLetter,
    DeliveryMetadata,
    MessageDelivery,
    PublishReceipt,
    RetryPolicy,
    Subscription,
    TransportEnvelope,
)


@dataclass(slots=True)
class _Inflight:
    sequence: int
    delivery_id: str
    consumer_id: str
    attempt: int


@dataclass(slots=True)
class _GroupState:
    cursor: int
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    active_consumer: str | None = None
    inflight: _Inflight | None = None
    attempts: dict[int, int] = field(default_factory=dict)
    retry_not_before: dict[int, float] = field(default_factory=dict)


@dataclass(slots=True)
class _TopicState:
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    messages: deque[TransportEnvelope] = field(default_factory=deque)
    base_sequence: int = 0
    next_sequence: int = 0
    groups: dict[str, _GroupState] = field(default_factory=dict)
    dead_letters: dict[str, list[DeadLetter]] = field(default_factory=dict)


class InProcessMessageTransport(MessageTransport):
    """Deterministic bounded in-process transport for single-node operation/tests.

    Delivery is at-least-once. Ordering is guaranteed per topic and consumer
    group by allowing one in-flight message per group. Different groups have no
    global ordering relationship.
    """

    def __init__(self, *, max_queue_size: int = 1024, provider_id: str = "in-process") -> None:
        if max_queue_size < 1:
            raise ValueError("max_queue_size must be >= 1")
        self._max_queue_size = max_queue_size
        self._provider_id = provider_id
        self._topics: dict[str, _TopicState] = {}
        self._accepting = True
        self._closing = False
        self._closed = False
        self._available = True

    @property
    def descriptor(self) -> ProviderDescriptor:
        if not self._available or self._closed:
            health = HealthStatus.UNAVAILABLE
        elif self._closing:
            health = HealthStatus.DEGRADED
        else:
            health = HealthStatus.HEALTHY
        return ProviderDescriptor(
            provider_id=self._provider_id,
            provider_type="message_transport",
            supported_operations=("publish", "subscribe", "ack", "nack", "dead_letters", "close"),
            capabilities=(
                Capability(
                    name="message_transport",
                    kind=CapabilityKind.EVENT,
                    supported_operations=("publish", "subscribe", "ack", "nack"),
                    features=(
                        "at_least_once",
                        "consumer_groups",
                        "dead_letter",
                        "bounded_backpressure",
                        "topic_group_ordering",
                        "operation_control_timeout",
                        "operation_idempotency_binding",
                    ),
                    limits={"max_queue_size": self._max_queue_size},
                ),
            ),
            health=health,
            available=self._available and not self._closed,
            limits={"max_queue_size": self._max_queue_size},
        )

    async def publish(
        self,
        topic: str,
        envelope: TransportEnvelope,
        *,
        control: OperationControl | None = None,
    ) -> PublishReceipt:
        if not topic.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "topic must not be blank")
        if control is not None and control.idempotency_key is not None:
            if envelope.idempotency_key != control.idempotency_key:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "operation idempotency_key must match envelope idempotency_key",
                    provider_id=self._provider_id,
                )
        operation = self._publish_once(topic, envelope)
        if control is None or control.timeout_seconds is None:
            return await operation
        try:
            return await asyncio.wait_for(operation, timeout=control.timeout_seconds)
        except TimeoutError as exc:
            raise ContractError(
                ErrorCode.TIMEOUT,
                "message publish timed out",
                retryable=True,
                provider_id=self._provider_id,
                details={"topic": topic},
            ) from exc

    async def _publish_once(self, topic: str, envelope: TransportEnvelope) -> PublishReceipt:
        self._require_available()
        if not self._accepting:
            raise ContractError(ErrorCode.UNAVAILABLE, "transport is closing", retryable=True)
        state = self._topic(topic)
        async with state.condition:
            if len(state.messages) >= self._max_queue_size:
                raise ContractError(
                    ErrorCode.RESOURCE_EXHAUSTED,
                    "transport queue is full",
                    retryable=True,
                    provider_id=self._provider_id,
                    details={"topic": topic, "max_queue_size": self._max_queue_size},
                )
            state.messages.append(envelope)
            state.next_sequence += 1
            state.condition.notify_all()
        return PublishReceipt(message_id=envelope.message_id, topic=topic)

    def subscribe(self, subscription: Subscription) -> MessageSubscription:
        return self._iterate(subscription)

    async def _iterate(self, subscription: Subscription) -> AsyncGenerator[MessageDelivery, None]:
        self._require_available()
        if self._closed:
            raise ContractError(ErrorCode.UNAVAILABLE, "transport is closed")
        state = self._topic(subscription.topic)
        group = state.groups.setdefault(
            subscription.consumer_group,
            _GroupState(cursor=state.base_sequence, retry_policy=subscription.retry_policy),
        )
        if group.active_consumer is None:
            group.retry_policy = subscription.retry_policy
        async with state.condition:
            if group.active_consumer is not None:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "consumer group already has an active consumer in reference transport",
                    details={
                        "topic": subscription.topic,
                        "consumer_group": subscription.consumer_group,
                    },
                )
            group.active_consumer = subscription.consumer_id

        try:
            while True:
                delivery: MessageDelivery | None = None
                async with state.condition:
                    self._require_available()
                    if group.inflight is not None:
                        raise ContractError(
                            ErrorCode.CONTRACT_VIOLATION,
                            "previous delivery must be acked or nacked before requesting another",
                        )
                    if group.cursor < state.next_sequence:
                        sequence = group.cursor
                        index = sequence - state.base_sequence
                        if index < 0 or index >= len(state.messages):
                            raise ContractError(
                                ErrorCode.CONTRACT_VIOLATION,
                                "consumer cursor is outside retained transport range",
                            )
                        retry_at = group.retry_not_before.get(sequence)
                        now = asyncio.get_running_loop().time()
                        if retry_at is not None and retry_at > now:
                            timeout = retry_at - now
                            try:
                                await asyncio.wait_for(state.condition.wait(), timeout=timeout)
                            except TimeoutError:
                                pass
                            continue
                        group.retry_not_before.pop(sequence, None)
                        attempt = group.attempts.get(sequence, 0) + 1
                        group.attempts[sequence] = attempt
                        inflight = _Inflight(
                            sequence=sequence,
                            delivery_id=f"delivery_{uuid4()}",
                            consumer_id=subscription.consumer_id,
                            attempt=attempt,
                        )
                        group.inflight = inflight
                        delivery = MessageDelivery(
                            envelope=state.messages[index],
                            metadata=DeliveryMetadata(
                                delivery_id=inflight.delivery_id,
                                topic=subscription.topic,
                                consumer_id=subscription.consumer_id,
                                consumer_group=subscription.consumer_group,
                                attempt=attempt,
                                redelivered=attempt > 1,
                            ),
                        )
                    elif self._closing:
                        break
                    else:
                        await state.condition.wait()
                        continue
                if delivery is not None:
                    yield delivery
        finally:
            async with state.condition:
                if (
                    group.inflight is not None
                    and group.inflight.consumer_id == subscription.consumer_id
                ):
                    group.inflight = None
                if group.active_consumer == subscription.consumer_id:
                    group.active_consumer = None
                state.condition.notify_all()

    async def ack(self, delivery: MessageDelivery) -> None:
        self._require_available()
        state, group = self._delivery_state(delivery)
        async with state.condition:
            inflight = self._require_matching_inflight(group, delivery)
            group.cursor = inflight.sequence + 1
            group.inflight = None
            group.attempts.pop(inflight.sequence, None)
            group.retry_not_before.pop(inflight.sequence, None)
            self._prune(state)
            self._finish_close_if_drained()
            state.condition.notify_all()

    async def nack(
        self,
        delivery: MessageDelivery,
        *,
        retry: bool = True,
        reason: str | None = None,
    ) -> None:
        self._require_available()
        state, group = self._delivery_state(delivery)
        async with state.condition:
            inflight = self._require_matching_inflight(group, delivery)
            subscription_attempts = delivery.metadata.attempt
            retry_policy = group.retry_policy
            should_retry = retry and subscription_attempts < retry_policy.max_attempts
            group.inflight = None
            if should_retry:
                delay = retry_policy.delay_for_retry(subscription_attempts)
                retry_at = asyncio.get_running_loop().time() + delay
                group.retry_not_before[inflight.sequence] = retry_at
            else:
                index = inflight.sequence - state.base_sequence
                if index < 0 or index >= len(state.messages):
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "dead-letter source is unavailable",
                    )
                dead_letter = DeadLetter(
                    envelope=state.messages[index],
                    topic=delivery.metadata.topic,
                    consumer_group=delivery.metadata.consumer_group,
                    attempts=subscription_attempts,
                    reason=reason or ("retry_exhausted" if retry else "rejected"),
                )
                group_dead_letters = state.dead_letters.setdefault(
                    delivery.metadata.consumer_group, []
                )
                group_dead_letters.append(dead_letter)
                group.cursor = inflight.sequence + 1
                group.attempts.pop(inflight.sequence, None)
                group.retry_not_before.pop(inflight.sequence, None)
                self._prune(state)
                self._finish_close_if_drained()
            state.condition.notify_all()

    async def dead_letters(self, topic: str, consumer_group: str) -> tuple[DeadLetter, ...]:
        self._require_available()
        state = self._topics.get(topic)
        if state is None:
            return ()
        async with state.condition:
            letters = state.dead_letters.get(consumer_group)
            return () if letters is None else tuple(letters)

    async def close(self, *, graceful: bool = True) -> None:
        self._accepting = False
        self._closing = True
        if not graceful:
            for state in self._topics.values():
                async with state.condition:
                    state.messages.clear()
                    state.base_sequence = state.next_sequence
                    for group in state.groups.values():
                        group.cursor = state.next_sequence
                        group.inflight = None
                        group.attempts.clear()
                        group.retry_not_before.clear()
                    state.condition.notify_all()
            self._closed = True
            return
        self._finish_close_if_drained()
        for state in self._topics.values():
            async with state.condition:
                state.condition.notify_all()

    async def set_available(self, available: bool) -> None:
        """Reference-test hook for deterministic outage/recovery simulation."""
        self._available = available
        for state in self._topics.values():
            async with state.condition:
                state.condition.notify_all()

    def _topic(self, topic: str) -> _TopicState:
        return self._topics.setdefault(topic, _TopicState())

    def _delivery_state(self, delivery: MessageDelivery) -> tuple[_TopicState, _GroupState]:
        state = self._topics.get(delivery.metadata.topic)
        if state is None:
            raise ContractError(ErrorCode.NOT_FOUND, "delivery topic is not known")
        group = state.groups.get(delivery.metadata.consumer_group)
        if group is None:
            raise ContractError(ErrorCode.NOT_FOUND, "delivery consumer group is not known")
        return state, group

    def _require_matching_inflight(
        self,
        group: _GroupState,
        delivery: MessageDelivery,
    ) -> _Inflight:
        inflight = group.inflight
        if inflight is None or inflight.delivery_id != delivery.metadata.delivery_id:
            raise ContractError(ErrorCode.CONFLICT, "delivery is not the current in-flight message")
        return inflight

    def _require_available(self) -> None:
        if not self._available:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "message transport is unavailable",
                retryable=True,
                provider_id=self._provider_id,
            )

    def _prune(self, state: _TopicState) -> None:
        if not state.groups:
            return
        minimum_cursor = min(group.cursor for group in state.groups.values())
        while state.messages and state.base_sequence < minimum_cursor:
            state.messages.popleft()
            state.base_sequence += 1

    def _finish_close_if_drained(self) -> None:
        if not self._closing:
            return
        drained = all(
            not state.messages and all(group.inflight is None for group in state.groups.values())
            for state in self._topics.values()
        )
        if drained:
            self._closed = True
