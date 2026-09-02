from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationControl
from ai_multi_agent_platform.domain import Event, new_id
from ai_multi_agent_platform.messaging import (
    ENVELOPE_VERSION,
    IdempotentConsumer,
    InProcessMessageTransport,
    MessageKind,
    MessageTransport,
    MessageTransportContractSuite,
    PublishReceipt,
    RetryPolicy,
    Subscription,
    TraceContext,
    TransportEnvelope,
    envelope_for_domain_event,
)


def _envelope(sequence: int) -> TransportEnvelope:
    return TransportEnvelope(
        message_type="test.command",
        kind=MessageKind.COMMAND,
        payload_schema_version="1.0",
        source_component="tests",
        correlation_id="corr-1",
        causation_id="cause-1",
        idempotency_key=f"command-{sequence}",
        trace_context=TraceContext(trace_id="trace-1", span_id="span-1"),
        payload={"sequence": sequence},
    )


def test_transport_envelope_is_versioned_and_detached_from_caller_mutation() -> None:
    payload = {"items": [1, 2]}
    attributes = {"routing": {"priority": "normal"}}
    envelope = TransportEnvelope(
        message_type="test.signal",
        kind=MessageKind.SIGNAL,
        payload_schema_version="1.0",
        source_component="tests",
        correlation_id="corr-1",
        payload=payload,
        attributes=attributes,
    )
    payload["items"].append(3)
    attributes["routing"]["priority"] = "changed"

    assert envelope.envelope_version == ENVELOPE_VERSION
    assert isinstance(envelope.payload, Mapping)
    assert envelope.payload["items"] == (1, 2)
    assert isinstance(envelope.attributes["routing"], Mapping)
    assert envelope.attributes["routing"]["priority"] == "normal"

    with pytest.raises(ValueError, match="exactly one"):
        TransportEnvelope(
            message_type="invalid",
            kind=MessageKind.SIGNAL,
            payload_schema_version="1.0",
            source_component="tests",
            correlation_id="corr-1",
        )

    with pytest.raises(ValueError, match="unsupported envelope_version"):
        TransportEnvelope(
            message_type="invalid.version",
            kind=MessageKind.SIGNAL,
            payload_schema_version="1.0",
            source_component="tests",
            correlation_id="corr-1",
            envelope_version="2.0",
            payload={"ok": True},
        )


def test_transport_envelope_wire_round_trip_validates_against_schema() -> None:
    envelope = TransportEnvelope(
        message_type="test.command",
        kind=MessageKind.COMMAND,
        payload_schema_version="1.0",
        source_component="tests",
        correlation_id="corr-1",
        causation_id="cause-1",
        project_id=new_id("project"),
        task_id=new_id("task"),
        run_id=new_id("run"),
        idempotency_key="wire-roundtrip-1",
        trace_context=TraceContext(
            trace_id="trace-1",
            span_id="span-1",
            trace_flags="01",
            tracestate="vendor=value",
            baggage={"tenant": "test"},
        ),
        attributes={"routing": {"priority": "normal"}},
        payload={"nested": [1, {"ok": True}]},
    )
    schema_path = Path(__file__).parents[1] / "schemas" / "transport" / "envelope.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    wire = envelope.to_dict()
    validator.validate(wire)
    restored = TransportEnvelope.from_dict(wire)

    assert restored == envelope
    validator.validate(restored.to_dict())


def test_publish_subscribe_preserves_correlation_causation_and_trace() -> None:
    async def scenario() -> None:
        transport = InProcessMessageTransport()
        envelope = _envelope(1)
        receipt = await transport.publish("commands", envelope)
        stream = transport.subscribe(Subscription("commands", "consumer-a", "workers"))
        delivery = await anext(stream)

        assert receipt.message_id == envelope.message_id
        assert delivery.envelope.correlation_id == "corr-1"
        assert delivery.envelope.causation_id == "cause-1"
        assert delivery.envelope.trace_context.trace_id == "trace-1"
        assert delivery.envelope.trace_context.span_id == "span-1"
        assert delivery.metadata.consumer_id == "consumer-a"
        assert delivery.metadata.consumer_group == "workers"
        assert delivery.metadata.attempt == 1

        await transport.ack(delivery)
        await stream.aclose()
        await transport.close(graceful=True)

    asyncio.run(scenario())


def test_duplicate_delivery_is_safe_with_idempotent_consumer_helper() -> None:
    async def scenario() -> None:
        transport = InProcessMessageTransport()
        envelope = _envelope(1)
        await transport.publish("commands", envelope)
        await transport.publish("commands", envelope)
        stream = transport.subscribe(Subscription("commands", "consumer-a", "workers"))
        consumer = IdempotentConsumer(transport)
        handled: list[str] = []

        async def handler(message: TransportEnvelope) -> None:
            handled.append(message.message_id)

        first = await anext(stream)
        assert await consumer.handle(first, handler) is True
        duplicate = await anext(stream)
        assert duplicate.envelope.message_id == first.envelope.message_id
        assert await consumer.handle(duplicate, handler) is False
        assert handled == [envelope.message_id]

        await stream.aclose()
        await transport.close(graceful=True)

    asyncio.run(scenario())


def test_concurrent_duplicates_do_not_run_handler_concurrently() -> None:
    async def scenario() -> None:
        transport = InProcessMessageTransport()
        envelope = _envelope(1)
        await transport.publish("commands", envelope)
        stream_a = transport.subscribe(Subscription("commands", "consumer-a", "group-a"))
        stream_b = transport.subscribe(Subscription("commands", "consumer-b", "group-b"))
        delivery_a = await anext(stream_a)
        delivery_b = await anext(stream_b)
        consumer = IdempotentConsumer(transport)
        handled: list[str] = []
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(message: TransportEnvelope) -> None:
            handled.append(message.message_id)
            started.set()
            await release.wait()

        first_task = asyncio.create_task(consumer.handle(delivery_a, handler))
        await started.wait()
        duplicate_task = asyncio.create_task(consumer.handle(delivery_b, handler))
        await asyncio.sleep(0)
        assert duplicate_task.done() is False

        release.set()
        results = await asyncio.gather(first_task, duplicate_task)
        assert sorted(results) == [False, True]
        assert handled == [envelope.message_id]

        await stream_a.aclose()
        await stream_b.aclose()
        await transport.close(graceful=True)

    asyncio.run(scenario())


def test_unacked_delivery_is_redelivered_after_consumer_restart() -> None:
    async def scenario() -> None:
        transport = InProcessMessageTransport()
        await transport.publish("commands", _envelope(1))
        first_stream = transport.subscribe(Subscription("commands", "consumer-a", "workers"))
        first = await anext(first_stream)
        await first_stream.aclose()

        second_stream = transport.subscribe(Subscription("commands", "consumer-b", "workers"))
        second = await anext(second_stream)
        assert second.envelope.message_id == first.envelope.message_id
        assert second.metadata.attempt == 2
        assert second.metadata.redelivered is True

        await transport.ack(second)
        await second_stream.aclose()
        await transport.close(graceful=True)

    asyncio.run(scenario())


def test_failed_consumer_retries_then_dead_letters_poison_message() -> None:
    async def scenario() -> None:
        transport = InProcessMessageTransport()
        await transport.publish("commands", _envelope(1))
        stream = transport.subscribe(
            Subscription(
                "commands",
                "consumer-a",
                "workers",
                retry_policy=RetryPolicy(max_attempts=2),
            )
        )

        first = await anext(stream)
        await transport.nack(first, retry=True, reason="temporary")
        second = await anext(stream)
        assert second.metadata.attempt == 2
        await transport.nack(second, retry=True, reason="poison")

        dead_letters = await transport.dead_letters("commands", "workers")
        assert len(dead_letters) == 1
        assert dead_letters[0].envelope.message_id == first.envelope.message_id
        assert dead_letters[0].attempts == 2
        assert dead_letters[0].reason == "poison"

        await stream.aclose()
        await transport.close(graceful=True)

    asyncio.run(scenario())


def test_defined_ordering_scope_is_topic_and_consumer_group() -> None:
    async def scenario() -> None:
        transport = InProcessMessageTransport()
        expected = [_envelope(index) for index in range(3)]
        for envelope in expected:
            await transport.publish("ordered", envelope)

        stream = transport.subscribe(Subscription("ordered", "consumer-a", "group-a"))
        received: list[str] = []
        for _ in expected:
            delivery = await anext(stream)
            received.append(delivery.envelope.message_id)
            await transport.ack(delivery)

        assert received == [envelope.message_id for envelope in expected]
        await stream.aclose()
        await transport.close(graceful=True)

    asyncio.run(scenario())


def test_transport_unavailable_maps_to_canonical_retryable_error() -> None:
    async def scenario() -> None:
        transport = InProcessMessageTransport()
        await transport.set_available(False)
        with pytest.raises(ContractError) as exc_info:
            await transport.publish("commands", _envelope(1))
        assert exc_info.value.code is ErrorCode.UNAVAILABLE
        assert exc_info.value.retryable is True

    asyncio.run(scenario())


def test_bounded_queue_applies_explicit_backpressure() -> None:
    async def scenario() -> None:
        transport = InProcessMessageTransport(max_queue_size=1)
        await transport.publish("commands", _envelope(1))
        with pytest.raises(ContractError) as exc_info:
            await transport.publish("commands", _envelope(2))
        assert exc_info.value.code is ErrorCode.RESOURCE_EXHAUSTED
        assert exc_info.value.retryable is True
        await transport.close(graceful=False)

    asyncio.run(scenario())


def test_publish_operation_control_binds_idempotency_key() -> None:
    async def scenario() -> None:
        transport = InProcessMessageTransport()
        envelope = _envelope(1)
        receipt = await transport.publish(
            "commands",
            envelope,
            control=OperationControl(idempotency_key=envelope.idempotency_key),
        )
        assert receipt.message_id == envelope.message_id

        with pytest.raises(ContractError) as exc_info:
            await transport.publish(
                "commands",
                envelope,
                control=OperationControl(idempotency_key="different-key"),
            )
        assert exc_info.value.code is ErrorCode.INVALID_REQUEST
        await transport.close(graceful=False)

    asyncio.run(scenario())


def test_publish_operation_control_timeout_is_enforced() -> None:
    class SlowTransport(InProcessMessageTransport):
        async def _publish_once(
            self, topic: str, envelope: TransportEnvelope
        ) -> PublishReceipt:
            await asyncio.sleep(0.05)
            return await super()._publish_once(topic, envelope)

    async def scenario() -> None:
        transport = SlowTransport()
        with pytest.raises(ContractError) as exc_info:
            await transport.publish(
                "commands",
                _envelope(1),
                control=OperationControl(timeout_seconds=0.01),
            )
        assert exc_info.value.code is ErrorCode.TIMEOUT
        assert exc_info.value.retryable is True
        await transport.close(graceful=False)

    asyncio.run(scenario())


def test_graceful_shutdown_rejects_publish_and_allows_pending_delivery_to_drain() -> None:
    async def scenario() -> None:
        transport = InProcessMessageTransport()
        await transport.publish("commands", _envelope(1))
        await transport.close(graceful=True)

        with pytest.raises(ContractError) as exc_info:
            await transport.publish("commands", _envelope(2))
        assert exc_info.value.code is ErrorCode.UNAVAILABLE

        stream = transport.subscribe(Subscription("commands", "consumer-a", "workers"))
        delivery = await anext(stream)
        await transport.ack(delivery)
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        await stream.aclose()

    asyncio.run(scenario())


def test_domain_event_envelope_references_canonical_history_without_copying_ownership() -> None:
    event = Event(
        event_type="run.started",
        subject_type="run",
        subject_id=new_id("run"),
        correlation_id="corr-1",
        causation_id="cause-1",
        trace_id="trace-1",
        project_id=new_id("project"),
        payload={"status": "running"},
    )

    envelope = envelope_for_domain_event(event, source_component="kernel")

    assert envelope.kind is MessageKind.DOMAIN_EVENT
    assert envelope.payload_ref == f"canonical-event:{event.id}"
    assert envelope.payload is None
    assert envelope.idempotency_key == event.id
    assert envelope.project_id == event.project_id
    assert envelope.run_id == event.subject_id
    assert envelope.correlation_id == event.correlation_id
    assert envelope.causation_id == event.causation_id
    assert envelope.trace_context.trace_id == event.trace_id


def test_reference_transport_passes_reusable_contract_suite() -> None:
    async def availability_toggle(transport: MessageTransport, available: bool) -> None:
        assert isinstance(transport, InProcessMessageTransport)
        await transport.set_available(available)

    checks = asyncio.run(
        MessageTransportContractSuite(
            lambda: InProcessMessageTransport(),
            bounded_factory=lambda: InProcessMessageTransport(max_queue_size=1),
            availability_toggle=availability_toggle,
        ).run()
    )
    assert checks == (
        "descriptor_semantics",
        "publish_subscribe_metadata",
        "operation_control_binding",
        "redelivery_ordering",
        "idempotent_consumer",
        "retry_backoff",
        "consumer_restart",
        "dead_letter",
        "transport_unavailable",
        "bounded_backpressure",
        "graceful_shutdown",
    )
