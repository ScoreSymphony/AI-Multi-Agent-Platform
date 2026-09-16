from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.distributed.transport import (
    WORKER_REPLY_TOPIC_PREFIX,
    WorkerTransportEndpoint,
)
from ai_multi_agent_platform.distributed.workspace_remote_materializer import (
    TransportRemoteWorkspaceMaterializer,
)
from ai_multi_agent_platform.distributed.workspace_transport_contract import (
    WORKSPACE_REPLY_TOPIC_PREFIX,
)
from ai_multi_agent_platform.distributed.workspace_transport_endpoint import (
    WorkerWorkspaceTransportEndpoint,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.messaging import (
    DeliveryMetadata,
    MessageDelivery,
    MessageKind,
    PublishReceipt,
    Subscription,
    TransportEnvelope,
)


class _OneShotSubscription:
    def __init__(self, delivery: MessageDelivery) -> None:
        self._delivery = delivery
        self.closed = False

    def __aiter__(self) -> _OneShotSubscription:
        return self

    async def __anext__(self) -> MessageDelivery:
        if self._delivery is None:
            raise StopAsyncIteration
        delivery = self._delivery
        self._delivery = None  # type: ignore[assignment]
        return delivery

    async def aclose(self) -> None:
        self.closed = True


class _BoundaryTransport:
    def __init__(self, delivery: MessageDelivery) -> None:
        self.subscription = _OneShotSubscription(delivery)
        self.acks: list[MessageDelivery] = []
        self.nacks: list[tuple[MessageDelivery, bool, str | None]] = []
        self.published: list[tuple[str, TransportEnvelope]] = []

    def subscribe(self, subscription: Subscription) -> _OneShotSubscription:
        del subscription
        return self.subscription

    async def publish(
        self,
        topic: str,
        envelope: TransportEnvelope,
        *,
        control: object | None = None,
    ) -> PublishReceipt:
        del control
        self.published.append((topic, envelope))
        return PublishReceipt(message_id=envelope.message_id, topic=topic)

    async def ack(self, delivery: MessageDelivery) -> None:
        self.acks.append(delivery)

    async def nack(
        self,
        delivery: MessageDelivery,
        *,
        retry: bool = True,
        reason: str | None = None,
    ) -> None:
        self.nacks.append((delivery, retry, reason))


class _QueueSubscription:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[MessageDelivery] = asyncio.Queue()
        self.closed = False

    def __aiter__(self) -> _QueueSubscription:
        return self

    async def __anext__(self) -> MessageDelivery:
        return await self.queue.get()

    async def aclose(self) -> None:
        self.closed = True


class _WorkspaceReplyTransport:
    def __init__(self, worker_id: str, *, error_code: str | None) -> None:
        self.worker_id = worker_id
        self.error_code = error_code
        self.subscription = _QueueSubscription()
        self.acks: list[MessageDelivery] = []

    def subscribe(self, subscription: Subscription) -> _QueueSubscription:
        del subscription
        return self.subscription

    async def publish(
        self,
        topic: str,
        envelope: TransportEnvelope,
        *,
        control: object | None = None,
    ) -> PublishReceipt:
        del topic, control
        if self.error_code is not None:
            reply = TransportEnvelope(
                message_type="workspace.error",
                kind=MessageKind.SIGNAL,
                payload_schema_version=envelope.payload_schema_version,
                source_component="workspace-worker:test",
                correlation_id=envelope.correlation_id,
                causation_id=envelope.message_id,
                payload={
                    "worker_id": self.worker_id,
                    "error_code": self.error_code,
                    "message": "safe workspace failure",
                    "retryable": True,
                },
            )
            await self.subscription.queue.put(_delivery(reply))
        return PublishReceipt(message_id=envelope.message_id, topic="command")

    async def ack(self, delivery: MessageDelivery) -> None:
        self.acks.append(delivery)


@dataclass
class _Dispatcher:
    worker_id: str
    failure: Exception | None = None

    async def dispatch(self, job: object) -> object:
        del job
        raise AssertionError("dispatch is not used by these tests")

    async def get(self, worker_job_id: str) -> object:
        del worker_job_id
        if self.failure is not None:
            raise self.failure
        raise AssertionError("a failure is required")

    async def cancel(self, worker_job_id: str) -> object:
        del worker_job_id
        raise AssertionError("cancel is not used by these tests")


@dataclass
class _WorkspaceStore:
    worker_id: str


class _FailingWorkerEndpoint(WorkerTransportEndpoint):
    def __init__(
        self, dispatcher: _Dispatcher, transport: _BoundaryTransport, failure: BaseException
    ):
        super().__init__(dispatcher, transport)
        self.failure = failure

    async def _handle(self, command: TransportEnvelope) -> None:
        del command
        raise self.failure


class _FailingWorkspaceEndpoint(WorkerWorkspaceTransportEndpoint):
    def __init__(
        self, store: _WorkspaceStore, transport: _BoundaryTransport, failure: BaseException
    ):
        super().__init__(store, transport)  # type: ignore[arg-type]
        self.failure = failure

    async def _handle(self, command: TransportEnvelope) -> None:
        del command
        raise self.failure


class _TypedWorkspaceEndpoint(WorkerWorkspaceTransportEndpoint):
    def __init__(self, store: _WorkspaceStore, transport: _BoundaryTransport, failure: Exception):
        super().__init__(store, transport)  # type: ignore[arg-type]
        self.failure = failure

    async def _dispatch(self, operation: str, data: object) -> dict[str, object]:
        del operation, data
        raise self.failure


def _delivery(envelope: TransportEnvelope | None = None) -> MessageDelivery:
    envelope = envelope or TransportEnvelope(
        message_type="test.command",
        kind=MessageKind.COMMAND,
        payload_schema_version="1",
        source_component="test",
        correlation_id="distributed-error-boundary-test",
        payload={},
    )
    return MessageDelivery(
        envelope=envelope,
        metadata=DeliveryMetadata(
            delivery_id="delivery-test",
            topic="test.topic",
            consumer_id="test-consumer",
            consumer_group="test-group",
            attempt=1,
            redelivered=False,
        ),
    )


def _worker_command(worker_id: str, worker_job_id: str) -> TransportEnvelope:
    return TransportEnvelope(
        message_type="worker.get",
        kind=MessageKind.COMMAND,
        payload_schema_version="1",
        source_component="test-control",
        correlation_id="worker-error-boundary-test",
        payload={
            "worker_id": worker_id,
            "worker_job_id": worker_job_id,
            "operation": "get",
            "reply_topic": f"{WORKER_REPLY_TOPIC_PREFIX}.test",
        },
    )


def _workspace_command(worker_id: str) -> TransportEnvelope:
    return TransportEnvelope(
        message_type="workspace.prepare",
        kind=MessageKind.COMMAND,
        payload_schema_version="1",
        source_component="test-control",
        correlation_id="workspace-error-boundary-test",
        payload={
            "worker_id": worker_id,
            "operation": "prepare",
            "reply_topic": f"{WORKSPACE_REPLY_TOPIC_PREFIX}.test",
        },
    )


def test_worker_delivery_boundary_retries_only_explicit_operational_failures() -> None:
    async def scenario() -> None:
        worker_id = new_id("worker")
        cases: tuple[tuple[Exception, bool], ...] = (
            (RuntimeError("programming bug"), False),
            (ConnectionError("transport unavailable"), True),
            (
                ContractError(
                    ErrorCode.RESOURCE_EXHAUSTED,
                    "worker capacity exhausted",
                    retryable=True,
                ),
                True,
            ),
            (ContractError(ErrorCode.CONTRACT_VIOLATION, "bad contract"), False),
        )
        for failure, expected_retry in cases:
            delivery = _delivery()
            transport = _BoundaryTransport(delivery)
            endpoint = _FailingWorkerEndpoint(
                _Dispatcher(worker_id),
                transport,
                failure,
            )
            await endpoint.serve()
            assert transport.acks == []
            assert transport.nacks == [
                (delivery, expected_retry, "worker_transport_boundary_failed")
            ]
            assert transport.subscription.closed is True

    asyncio.run(scenario())


def test_worker_delivery_boundary_does_not_swallow_task_cancellation() -> None:
    async def scenario() -> None:
        worker_id = new_id("worker")
        transport = _BoundaryTransport(_delivery())
        endpoint = _FailingWorkerEndpoint(
            _Dispatcher(worker_id),
            transport,
            asyncio.CancelledError(),
        )

        with pytest.raises(asyncio.CancelledError):
            await endpoint.serve()

        assert transport.acks == []
        assert transport.nacks == []
        assert transport.subscription.closed is True

    asyncio.run(scenario())


def test_worker_error_reply_preserves_contract_retryability() -> None:
    async def scenario() -> None:
        worker_id = new_id("worker")
        worker_job_id = new_id("worker_job")
        transport = _BoundaryTransport(_delivery())
        endpoint = WorkerTransportEndpoint(
            _Dispatcher(
                worker_id,
                ContractError(
                    ErrorCode.RESOURCE_EXHAUSTED,
                    "capacity",
                    retryable=True,
                ),
            ),
            transport,
        )

        await endpoint._handle(_worker_command(worker_id, worker_job_id))

        reply = transport.published[-1][1]
        assert reply.message_type == "worker.error"
        assert reply.payload is not None
        assert reply.payload["error_category"] == ErrorCode.RESOURCE_EXHAUSTED.value
        assert reply.payload["retryable"] is True

    asyncio.run(scenario())


def test_workspace_worker_error_reply_is_canonical_and_unknown_failure_is_terminal() -> None:
    async def scenario() -> None:
        worker_id = new_id("worker")
        for failure, expected_code, expected_retry in (
            (
                ContractError(ErrorCode.TIMEOUT, "workspace timeout", retryable=True),
                ErrorCode.TIMEOUT.value,
                True,
            ),
            (RuntimeError("private implementation detail"), ErrorCode.BACKEND_ERROR.value, False),
        ):
            transport = _BoundaryTransport(_delivery())
            endpoint = _TypedWorkspaceEndpoint(
                _WorkspaceStore(worker_id),
                transport,
                failure,
            )
            await endpoint._handle(_workspace_command(worker_id))
            reply = transport.published[-1][1]
            assert reply.message_type == "workspace.error"
            assert reply.payload is not None
            assert reply.payload["error_code"] == expected_code
            assert reply.payload["retryable"] is expected_retry
            if isinstance(failure, RuntimeError):
                assert "private implementation detail" not in str(reply.payload)

    asyncio.run(scenario())


def test_remote_workspace_error_reply_becomes_typed_contract_error() -> None:
    async def scenario() -> None:
        worker_id = new_id("worker")
        transport = _WorkspaceReplyTransport(worker_id, error_code=ErrorCode.TIMEOUT.value)
        materializer = TransportRemoteWorkspaceMaterializer(
            worker_id,
            transport,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            lambda workspace: workspace,  # type: ignore[arg-type,return-value]
            response_timeout_seconds=0.05,
        )

        with pytest.raises(ContractError) as caught:
            await materializer._send_request(
                correlation_id="workspace-control-test",
                operation="prepare",
                payload={},
                idempotency_key="workspace-control-test:prepare",
            )

        assert caught.value.code is ErrorCode.TIMEOUT
        assert caught.value.retryable is True
        assert transport.acks
        assert transport.subscription.closed is True

    asyncio.run(scenario())


def test_remote_workspace_unknown_error_code_is_contract_violation() -> None:
    async def scenario() -> None:
        worker_id = new_id("worker")
        transport = _WorkspaceReplyTransport(worker_id, error_code="future_unknown_error")
        materializer = TransportRemoteWorkspaceMaterializer(
            worker_id,
            transport,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            lambda workspace: workspace,  # type: ignore[arg-type,return-value]
            response_timeout_seconds=0.05,
        )

        with pytest.raises(ContractError) as caught:
            await materializer._send_request(
                correlation_id="workspace-control-test",
                operation="prepare",
                payload={},
                idempotency_key="workspace-control-test:prepare",
            )

        assert caught.value.code is ErrorCode.CONTRACT_VIOLATION
        assert caught.value.retryable is False

    asyncio.run(scenario())


def test_remote_workspace_response_timeout_is_retryable_timeout() -> None:
    async def scenario() -> None:
        worker_id = new_id("worker")
        transport = _WorkspaceReplyTransport(worker_id, error_code=None)
        materializer = TransportRemoteWorkspaceMaterializer(
            worker_id,
            transport,  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            lambda workspace: workspace,  # type: ignore[arg-type,return-value]
            response_timeout_seconds=0.001,
        )

        with pytest.raises(ContractError) as caught:
            await materializer._send_request(
                correlation_id="workspace-control-timeout-test",
                operation="prepare",
                payload={},
                idempotency_key="workspace-control-timeout-test:prepare",
            )

        assert caught.value.code is ErrorCode.TIMEOUT
        assert caught.value.retryable is True
        assert transport.subscription.closed is True

    asyncio.run(scenario())
