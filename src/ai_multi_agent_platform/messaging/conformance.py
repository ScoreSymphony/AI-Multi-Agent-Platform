from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationControl

from .contracts import MessageTransport
from .helpers import IdempotentConsumer
from .models import MessageKind, RetryPolicy, Subscription, TraceContext, TransportEnvelope

type TransportFactory = Callable[[], MessageTransport]
type AvailabilityToggle = Callable[[MessageTransport, bool], Awaitable[None]]


class MessageTransportContractSuite:
    """Pytest-independent checks reusable by future transport adapters.

    Full compliance includes adapter-provided fixtures for outage simulation and
    a deliberately bounded transport instance. Those fixtures keep test-only
    controls out of the production ``MessageTransport`` interface.
    """

    def __init__(
        self,
        factory: TransportFactory,
        *,
        bounded_factory: TransportFactory,
        availability_toggle: AvailabilityToggle,
    ) -> None:
        self._factory = factory
        self._bounded_factory = bounded_factory
        self._availability_toggle = availability_toggle

    async def run(self) -> tuple[str, ...]:
        checks: list[str] = []
        await self._descriptor_semantics()
        checks.append("descriptor_semantics")
        await self._publish_subscribe_and_metadata()
        checks.append("publish_subscribe_metadata")
        await self._operation_control_binding()
        checks.append("operation_control_binding")
        await self._redelivery_and_ordering()
        checks.append("redelivery_ordering")
        await self._idempotent_consumer()
        checks.append("idempotent_consumer")
        await self._retry_backoff()
        checks.append("retry_backoff")
        await self._consumer_restart()
        checks.append("consumer_restart")
        await self._dead_letter()
        checks.append("dead_letter")
        await self._transport_unavailable()
        checks.append("transport_unavailable")
        await self._bounded_backpressure()
        checks.append("bounded_backpressure")
        await self._graceful_shutdown()
        checks.append("graceful_shutdown")
        return tuple(checks)

    async def _descriptor_semantics(self) -> None:
        transport = self._factory()
        descriptor = transport.descriptor
        assert descriptor.provider_type == "message_transport"
        assert {"publish", "subscribe", "ack", "nack"}.issubset(descriptor.supported_operations)
        capability_names = {capability.name for capability in descriptor.capabilities}
        assert "message_transport" in capability_names
        await transport.close(graceful=True)

    def _envelope(self, sequence: int) -> TransportEnvelope:
        return TransportEnvelope(
            message_type="conformance.test",
            kind=MessageKind.COMMAND,
            payload_schema_version="1.0",
            source_component="transport-conformance",
            correlation_id="conformance-correlation",
            causation_id="conformance-cause",
            idempotency_key=f"conformance-{sequence}",
            trace_context=TraceContext(
                trace_id="trace-1",
                span_id="span-1",
                trace_flags="01",
                tracestate="vendor=value",
                baggage={"project": "demo"},
            ),
            payload={"sequence": sequence},
        )

    async def _publish_subscribe_and_metadata(self) -> None:
        transport = self._factory()
        envelope = self._envelope(1)
        receipt = await transport.publish("conformance", envelope)
        assert receipt.message_id == envelope.message_id
        stream = transport.subscribe(Subscription("conformance", "consumer-a", "group-a"))
        delivery = await anext(stream)
        assert delivery.envelope == envelope
        assert delivery.metadata.attempt == 1
        assert delivery.metadata.consumer_group == "group-a"
        assert delivery.envelope.correlation_id == "conformance-correlation"
        assert delivery.envelope.causation_id == "conformance-cause"
        assert delivery.envelope.trace_context == envelope.trace_context
        await transport.ack(delivery)
        await stream.aclose()
        await transport.close(graceful=True)

    async def _operation_control_binding(self) -> None:
        transport = self._factory()
        envelope = self._envelope(1)
        await transport.publish(
            "control",
            envelope,
            control=OperationControl(idempotency_key=envelope.idempotency_key),
        )
        try:
            await transport.publish(
                "control",
                envelope,
                control=OperationControl(idempotency_key="different-key"),
            )
        except ContractError as exc:
            assert exc.code is ErrorCode.INVALID_REQUEST
        else:
            raise AssertionError("transport accepted mismatched idempotency keys")
        await transport.close(graceful=False)

    async def _redelivery_and_ordering(self) -> None:
        transport = self._factory()
        first = self._envelope(1)
        second = self._envelope(2)
        await transport.publish("ordered", first)
        await transport.publish("ordered", second)
        stream = transport.subscribe(Subscription("ordered", "consumer-a", "group-a"))
        delivery = await anext(stream)
        assert delivery.envelope.message_id == first.message_id
        await transport.nack(delivery, retry=True, reason="retry-test")
        duplicate = await anext(stream)
        assert duplicate.envelope.message_id == first.message_id
        assert duplicate.metadata.redelivered is True
        await transport.ack(duplicate)
        next_delivery = await anext(stream)
        assert next_delivery.envelope.message_id == second.message_id
        await transport.ack(next_delivery)
        await stream.aclose()
        await transport.close(graceful=True)

    async def _idempotent_consumer(self) -> None:
        transport = self._factory()
        envelope = self._envelope(1)
        await transport.publish("idempotent", envelope)
        await transport.publish("idempotent", envelope)
        stream = transport.subscribe(Subscription("idempotent", "consumer-a", "group-a"))
        consumer = IdempotentConsumer(transport)
        handled: list[str] = []

        async def handler(message: TransportEnvelope) -> None:
            handled.append(message.message_id)

        first = await anext(stream)
        assert await consumer.handle(first, handler) is True
        duplicate = await anext(stream)
        assert await consumer.handle(duplicate, handler) is False
        assert handled == [envelope.message_id]
        await stream.aclose()
        await transport.close(graceful=True)

    async def _retry_backoff(self) -> None:
        transport = self._factory()
        await transport.publish("backoff", self._envelope(1))
        stream = transport.subscribe(
            Subscription(
                "backoff",
                "consumer-a",
                "group-a",
                retry_policy=RetryPolicy(
                    max_attempts=2,
                    initial_backoff_seconds=0.05,
                    backoff_multiplier=2.0,
                    max_backoff_seconds=0.1,
                ),
            )
        )
        first = await anext(stream)
        await transport.nack(first, retry=True, reason="backoff-test")
        retry_task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.01)
        assert not retry_task.done(), "retry was redelivered before configured backoff elapsed"
        second = await asyncio.wait_for(retry_task, timeout=1.0)
        assert second.metadata.attempt == 2
        await transport.ack(second)
        await stream.aclose()
        await transport.close(graceful=True)

    async def _consumer_restart(self) -> None:
        transport = self._factory()
        envelope = self._envelope(1)
        await transport.publish("restart", envelope)
        first_stream = transport.subscribe(Subscription("restart", "consumer-a", "group-a"))
        first = await anext(first_stream)
        await first_stream.aclose()
        second_stream = transport.subscribe(Subscription("restart", "consumer-b", "group-a"))
        second = await anext(second_stream)
        assert second.envelope.message_id == first.envelope.message_id
        assert second.metadata.redelivered is True
        await transport.ack(second)
        await second_stream.aclose()
        await transport.close(graceful=True)

    async def _dead_letter(self) -> None:
        transport = self._factory()
        await transport.publish("poison", self._envelope(1))
        stream = transport.subscribe(
            Subscription(
                "poison",
                "consumer-a",
                "group-a",
                retry_policy=RetryPolicy(max_attempts=2),
            )
        )
        first = await anext(stream)
        await transport.nack(first, retry=True, reason="poison")
        second = await anext(stream)
        await transport.nack(second, retry=True, reason="poison")
        dead_letters = await transport.dead_letters("poison", "group-a")
        assert len(dead_letters) == 1
        assert dead_letters[0].envelope.message_id == first.envelope.message_id
        assert dead_letters[0].attempts == 2
        await stream.aclose()
        await transport.close(graceful=True)

    async def _transport_unavailable(self) -> None:
        async def assert_unavailable(operation: Awaitable[object], message: str) -> None:
            try:
                await operation
            except ContractError as exc:
                assert exc.code is ErrorCode.UNAVAILABLE
                assert exc.retryable is True
            else:
                raise AssertionError(message)

        transport = self._factory()
        await transport.publish("outage", self._envelope(1))
        stream = transport.subscribe(Subscription("outage", "consumer-a", "group-a"))
        delivery = await anext(stream)
        await self._availability_toggle(transport, False)
        assert transport.descriptor.available is False

        await assert_unavailable(
            transport.publish("outage", self._envelope(2)),
            "transport publish did not expose canonical unavailable behavior",
        )
        await assert_unavailable(
            transport.ack(delivery),
            "transport ack did not expose canonical unavailable behavior",
        )
        await assert_unavailable(
            transport.nack(delivery, retry=True, reason="outage"),
            "transport nack did not expose canonical unavailable behavior",
        )
        await assert_unavailable(
            transport.dead_letters("outage", "group-a"),
            "transport dead-letter read did not expose canonical unavailable behavior",
        )

        await self._availability_toggle(transport, True)
        await transport.ack(delivery)
        await stream.aclose()
        await transport.close(graceful=True)

        delivery_transport = self._factory()
        expected = self._envelope(3)
        await delivery_transport.publish("outage-delivery", expected)
        blocked_stream = delivery_transport.subscribe(
            Subscription("outage-delivery", "consumer-a", "group-a")
        )
        await self._availability_toggle(delivery_transport, False)
        try:
            await anext(blocked_stream)
        except ContractError as exc:
            assert exc.code is ErrorCode.UNAVAILABLE
            assert exc.retryable is True
        else:
            raise AssertionError("delivery acquisition succeeded while transport was unavailable")
        await blocked_stream.aclose()

        await self._availability_toggle(delivery_transport, True)
        recovered_stream = delivery_transport.subscribe(
            Subscription("outage-delivery", "consumer-b", "group-a")
        )
        recovered = await anext(recovered_stream)
        assert recovered.envelope.message_id == expected.message_id
        await delivery_transport.ack(recovered)
        await recovered_stream.aclose()
        await delivery_transport.close(graceful=True)

        shutdown_transport = self._factory()
        await self._availability_toggle(shutdown_transport, False)
        assert shutdown_transport.descriptor.available is False
        await shutdown_transport.close(graceful=False)

    async def _bounded_backpressure(self) -> None:
        transport = self._bounded_factory()
        await transport.publish("bounded", self._envelope(1))
        try:
            await transport.publish("bounded", self._envelope(2))
        except ContractError as exc:
            assert exc.code is ErrorCode.RESOURCE_EXHAUSTED
            assert exc.retryable is True
        else:
            raise AssertionError("bounded transport did not reject overload explicitly")
        await transport.close(graceful=False)

    async def _graceful_shutdown(self) -> None:
        transport = self._factory()
        await transport.publish("shutdown", self._envelope(1))
        await transport.close(graceful=True)
        stream = transport.subscribe(Subscription("shutdown", "consumer-a", "group-a"))
        delivery = await anext(stream)
        await transport.ack(delivery)
        try:
            await anext(stream)
        except StopAsyncIteration:
            pass
        else:
            raise AssertionError("graceful shutdown did not terminate drained subscription")
        await stream.aclose()
