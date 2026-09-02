from __future__ import annotations

from collections.abc import Callable

from .contracts import MessageTransport
from .models import MessageKind, RetryPolicy, Subscription, TransportEnvelope

type TransportFactory = Callable[[], MessageTransport]


class MessageTransportContractSuite:
    """Pytest-independent checks reusable by future transport adapters."""

    def __init__(self, factory: TransportFactory) -> None:
        self._factory = factory

    async def run(self) -> tuple[str, ...]:
        checks: list[str] = []
        await self._descriptor_semantics()
        checks.append("descriptor_semantics")
        await self._publish_subscribe_and_metadata()
        checks.append("publish_subscribe_metadata")
        await self._redelivery_and_ordering()
        checks.append("redelivery_ordering")
        await self._consumer_restart()
        checks.append("consumer_restart")
        await self._dead_letter()
        checks.append("dead_letter")
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
        await transport.ack(delivery)
        await stream.aclose()
        await transport.close(graceful=True)

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
