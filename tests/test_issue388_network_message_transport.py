from __future__ import annotations

import asyncio
import multiprocessing
from contextlib import suppress

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, ExecutionRequest, OperationContext
from ai_multi_agent_platform.distributed import (
    LocalWorker,
    TransportWorkerDispatcher,
    WorkerJobRequest,
    WorkerTransportEndpoint,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.messaging import (
    MessageKind,
    Subscription,
    TcpMessageBroker,
    TcpMessageTransport,
    TransportEnvelope,
)
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend

TEST_AUTHENTICATION_KEY = "issue-388-test-transport-credential"


def _envelope(sequence: int = 1) -> TransportEnvelope:
    return TransportEnvelope(
        message_type="issue388.test",
        kind=MessageKind.COMMAND,
        payload_schema_version="1",
        source_component="issue388-tests",
        correlation_id=f"issue388-{sequence}",
        idempotency_key=f"issue388-idempotency-{sequence}",
        payload={"sequence": sequence},
    )


def _job() -> WorkerJobRequest:
    task_id = new_id("task")
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=task_id,
            context=OperationContext(correlation_id=f"issue388:{task_id}"),
        )
    )


def _worker_process(host: str, port: int, worker_id: str, authentication_key: str) -> None:
    async def serve() -> None:
        transport = TcpMessageTransport(
            host,
            port,
            authentication_key=authentication_key,
            provider_id=f"process-worker:{worker_id}",
        )
        endpoint = WorkerTransportEndpoint(
            LocalWorker(worker_id, FakeLifecycleBackend()),
            transport,
        )
        try:
            await endpoint.serve()
        finally:
            await transport.close(graceful=False)

    asyncio.run(serve())


def test_loopback_transport_preserves_envelope_and_ack_semantics() -> None:
    async def scenario() -> None:
        broker = TcpMessageBroker(authentication_key=TEST_AUTHENTICATION_KEY)
        await broker.start()
        transport = TcpMessageTransport(
            broker.host,
            broker.port,
            authentication_key=TEST_AUTHENTICATION_KEY,
        )
        try:
            assert await transport.check_ready() is True
            envelope = _envelope()
            receipt = await transport.publish("issue388.loopback", envelope)
            stream = transport.subscribe(
                Subscription("issue388.loopback", "issue388-consumer", "issue388-group")
            )
            delivery = await asyncio.wait_for(anext(stream), timeout=2.0)
            assert receipt.message_id == envelope.message_id
            assert delivery.envelope == envelope
            assert delivery.metadata.attempt == 1
            await transport.ack(delivery)
            await stream.aclose()
        finally:
            await transport.close(graceful=False)
            await broker.close(graceful=False)

    asyncio.run(scenario())


def test_wrong_hmac_credential_is_rejected_without_exposing_secret_in_envelope() -> None:
    async def scenario() -> None:
        broker = TcpMessageBroker(authentication_key=TEST_AUTHENTICATION_KEY)
        await broker.start()
        transport = TcpMessageTransport(
            broker.host,
            broker.port,
            authentication_key="wrong-credential",
        )
        envelope = _envelope()
        try:
            with pytest.raises(ContractError) as error:
                await transport.publish("issue388.auth", envelope)
            assert error.value.code is ErrorCode.UNAUTHORIZED
            assert TEST_AUTHENTICATION_KEY not in repr(envelope.to_dict())
            assert "wrong-credential" not in repr(envelope.to_dict())
        finally:
            await transport.close(graceful=False)
            await broker.close(graceful=False)

    asyncio.run(scenario())


def test_non_loopback_network_boundaries_require_tls() -> None:
    with pytest.raises(ValueError, match="require TLS"):
        TcpMessageBroker(host="0.0.0.0", authentication_key=TEST_AUTHENTICATION_KEY)

    with pytest.raises(ValueError, match="require TLS"):
        TcpMessageTransport(
            "192.0.2.10",
            9443,
            authentication_key=TEST_AUTHENTICATION_KEY,
        )


def test_worker_dispatch_uses_real_tcp_boundary() -> None:
    worker_id = new_id("worker")

    async def scenario() -> None:
        broker = TcpMessageBroker(authentication_key=TEST_AUTHENTICATION_KEY)
        await broker.start()
        control_transport = TcpMessageTransport(
            broker.host,
            broker.port,
            authentication_key=TEST_AUTHENTICATION_KEY,
            provider_id="issue388-control",
        )
        worker_transport = TcpMessageTransport(
            broker.host,
            broker.port,
            authentication_key=TEST_AUTHENTICATION_KEY,
            provider_id="issue388-worker",
        )
        endpoint = WorkerTransportEndpoint(
            LocalWorker(worker_id, FakeLifecycleBackend()),
            worker_transport,
        )
        endpoint_task = asyncio.create_task(endpoint.serve())
        dispatcher = TransportWorkerDispatcher(
            worker_id,
            control_transport,
            response_timeout_seconds=3.0,
        )
        try:
            handle = await dispatcher.dispatch(_job())
            assert handle.run_id.startswith("run_")
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError, ContractError):
                await endpoint_task
            await worker_transport.close(graceful=False)
            await control_transport.close(graceful=False)
            await broker.close(graceful=False)

    asyncio.run(scenario())


def test_worker_endpoint_dispatches_across_an_independent_process_and_reconnects() -> None:
    worker_id = new_id("worker")

    async def scenario() -> None:
        broker = TcpMessageBroker(authentication_key=TEST_AUTHENTICATION_KEY)
        await broker.start()
        control_transport = TcpMessageTransport(
            broker.host,
            broker.port,
            authentication_key=TEST_AUTHENTICATION_KEY,
            provider_id="issue388-process-control",
        )
        dispatcher = TransportWorkerDispatcher(
            worker_id,
            control_transport,
            response_timeout_seconds=5.0,
        )
        context = multiprocessing.get_context("spawn")

        def start_worker() -> multiprocessing.Process:
            process = context.Process(
                target=_worker_process,
                args=(broker.host, broker.port, worker_id, TEST_AUTHENTICATION_KEY),
            )
            process.start()
            return process

        first_process = start_worker()
        second_process: multiprocessing.Process | None = None
        try:
            first = await dispatcher.dispatch(_job())
            assert first.run_id.startswith("run_")

            first_process.terminate()
            first_process.join(timeout=5)
            assert not first_process.is_alive()

            second_process = start_worker()
            second = await dispatcher.dispatch(_job())
            assert second.run_id.startswith("run_")
            assert second.run_id != first.run_id
        finally:
            if first_process.is_alive():
                first_process.terminate()
                first_process.join(timeout=5)
            if second_process is not None and second_process.is_alive():
                second_process.terminate()
                second_process.join(timeout=5)
            await control_transport.close(graceful=False)
            await broker.close(graceful=False)

    asyncio.run(scenario())
