"""Dependency-free cross-process MessageTransport adapter for issue #388.

The adapter remains an implementation of the platform-owned #35 transport
contract.  It never owns canonical Task/Run/Event/Node/Worker identity.  The
small broker retains transport-only delivery state and delegates ordering,
retry, backpressure and dead-letter behavior to the deterministic #35 reference
transport.
"""

from __future__ import annotations

import asyncio
import hmac
import ssl
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
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
from .models import DeadLetter, MessageDelivery, PublishReceipt, Subscription, TransportEnvelope
from .network_wire import (
    DEFAULT_FRAME_BYTES,
    as_mapping,
    authentication_mac,
    decode_dead_letter,
    decode_delivery,
    decode_receipt,
    decode_subscription,
    encode_dead_letter,
    encode_delivery,
    encode_receipt,
    encode_subscription,
    is_loopback_host,
    optional_boolean,
    optional_string,
    parse_datetime,
    read_frame,
    required_mapping,
    required_string,
    try_write_error,
    write_frame,
)
from .reference import InProcessMessageTransport

_DEFAULT_AUTH_SKEW = timedelta(minutes=5)
_SUBSCRIPTION_RECONNECT_ATTEMPTS = 3
_SUBSCRIPTION_RECONNECT_DELAY_SECONDS = 0.05
_DISCONNECT_POLL_SECONDS = 0.1


class TcpMessageBroker:
    """Self-hosted JSON-line broker exposing existing #35 semantics over TCP."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        max_queue_size: int = 1024,
        max_frame_bytes: int = DEFAULT_FRAME_BYTES,
        ssl_context: ssl.SSLContext | None = None,
        authentication_key: str | None = None,
        authentication_skew: timedelta = _DEFAULT_AUTH_SKEW,
        provider_id: str = "tcp-broker",
    ) -> None:
        if not host.strip():
            raise ValueError("TCP broker host must not be blank")
        if not 0 <= port <= 65535:
            raise ValueError("TCP broker port must be between 0 and 65535")
        if max_frame_bytes < 1024:
            raise ValueError("max_frame_bytes must be at least 1024")
        if authentication_key is not None and not authentication_key:
            raise ValueError("authentication_key must not be blank when provided")
        if authentication_skew <= timedelta(0):
            raise ValueError("authentication_skew must be positive")
        if not is_loopback_host(host):
            if ssl_context is None:
                raise ValueError("non-loopback TCP broker listeners require TLS")
            if authentication_key is None and ssl_context.verify_mode != ssl.CERT_REQUIRED:
                raise ValueError(
                    "non-loopback TCP broker listeners require HMAC authentication or mTLS"
                )
        self._host = host
        self._port = port
        self._max_frame_bytes = max_frame_bytes
        self._ssl_context = ssl_context
        self._authentication_key = authentication_key
        self._authentication_skew = authentication_skew
        self._provider_id = provider_id
        self._backend = InProcessMessageTransport(
            max_queue_size=max_queue_size,
            provider_id=f"{provider_id}:delivery",
        )
        self._server: asyncio.Server | None = None
        self._connections: set[asyncio.StreamWriter] = set()
        self._seen_auth_nonces: dict[str, datetime] = {}
        self._closed = False

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        if self._server is None or not self._server.sockets:
            return self._port
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def started(self) -> bool:
        return self._server is not None and self._server.is_serving()

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("TCP broker is closed")
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_connection,
            self._host,
            self._port,
            ssl=self._ssl_context,
            limit=self._max_frame_bytes + 1,
        )

    async def serve_forever(self) -> None:
        await self.start()
        if self._server is None:
            raise RuntimeError("TCP broker failed to start")
        async with self._server:
            await self._server.serve_forever()

    async def close(self, *, graceful: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        await self._backend.close(graceful=graceful)
        if not graceful:
            writers = tuple(self._connections)
            for writer in writers:
                writer.close()
            if writers:
                await asyncio.gather(
                    *(writer.wait_closed() for writer in writers),
                    return_exceptions=True,
                )

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._connections.add(writer)
        try:
            request = await read_frame(reader, self._max_frame_bytes)
            if request is None:
                return
            self._authenticate(request, writer)
            operation = required_string(request, "op")
            if operation == "subscribe":
                await self._serve_subscription(request, reader, writer)
                return
            response = await self._handle_request(operation, request)
            await write_frame(writer, {"ok": True, "result": response})
        except ContractError as exc:
            await try_write_error(writer, exc)
        except Exception:
            await try_write_error(
                writer,
                ContractError(
                    ErrorCode.BACKEND_ERROR,
                    "network message broker request failed",
                    retryable=True,
                    provider_id=self._provider_id,
                ),
            )
        finally:
            self._connections.discard(writer)
            writer.close()
            with suppress(ConnectionError, OSError, ssl.SSLError):
                await writer.wait_closed()

    async def _handle_request(
        self,
        operation: str,
        request: Mapping[str, object],
    ) -> object:
        if operation == "ping":
            return {"provider_id": self._provider_id}
        if operation == "publish":
            topic = required_string(request, "topic")
            envelope = TransportEnvelope.from_dict(required_mapping(request, "envelope"))
            receipt = await self._backend.publish(topic, envelope)
            return encode_receipt(receipt)
        if operation == "dead_letters":
            topic = required_string(request, "topic")
            consumer_group = required_string(request, "consumer_group")
            letters = await self._backend.dead_letters(topic, consumer_group)
            return [encode_dead_letter(item) for item in letters]
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"unsupported network transport operation: {operation}",
            provider_id=self._provider_id,
        )

    async def _serve_subscription(
        self,
        request: Mapping[str, object],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        subscription = decode_subscription(required_mapping(request, "subscription"))
        stream = self._backend.subscribe(subscription)
        await write_frame(writer, {"ok": True, "result": {"subscribed": True}})
        try:
            while True:
                delivery_task = asyncio.create_task(anext(stream))
                while not delivery_task.done():
                    done, _ = await asyncio.wait(
                        {delivery_task},
                        timeout=_DISCONNECT_POLL_SECONDS,
                    )
                    if done:
                        break
                    if reader.at_eof() or writer.is_closing():
                        delivery_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await delivery_task
                        return
                try:
                    delivery = delivery_task.result()
                except StopAsyncIteration:
                    return
                await write_frame(writer, {"delivery": encode_delivery(delivery)})
                action = await read_frame(reader, self._max_frame_bytes)
                if action is None:
                    return
                action_name = required_string(action, "action")
                delivery_id = required_string(action, "delivery_id")
                if delivery_id != delivery.metadata.delivery_id:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "delivery acknowledgement does not match current delivery",
                        provider_id=self._provider_id,
                    )
                if action_name == "ack":
                    await self._backend.ack(delivery)
                elif action_name == "nack":
                    retry = optional_boolean(action.get("retry"), "retry", default=True)
                    reason = optional_string(action.get("reason"), "reason")
                    await self._backend.nack(delivery, retry=retry, reason=reason)
                else:
                    raise ContractError(
                        ErrorCode.INVALID_REQUEST,
                        f"unsupported subscription action: {action_name}",
                        provider_id=self._provider_id,
                    )
                await write_frame(writer, {"ok": True, "result": {"advanced": True}})
        finally:
            await stream.aclose()

    def _authenticate(self, request: Mapping[str, object], writer: asyncio.StreamWriter) -> None:
        if self._authentication_key is None:
            if is_loopback_host(self._host):
                return
            ssl_object = writer.get_extra_info("ssl_object")
            if not isinstance(ssl_object, ssl.SSLObject) or not ssl_object.getpeercert():
                raise ContractError(
                    ErrorCode.UNAUTHORIZED,
                    "network transport client identity is required",
                    provider_id=self._provider_id,
                )
            return

        auth = required_mapping(request, "auth")
        nonce = required_string(auth, "nonce")
        issued_at_raw = required_string(auth, "issued_at")
        mac = required_string(auth, "mac")
        issued_at = parse_datetime(issued_at_raw, "auth.issued_at")
        now = datetime.now(UTC)
        if abs(now - issued_at) > self._authentication_skew:
            raise ContractError(
                ErrorCode.UNAUTHORIZED,
                "network transport authentication proof is outside the allowed time window",
                provider_id=self._provider_id,
            )
        self._prune_auth_nonces(now)
        if nonce in self._seen_auth_nonces:
            raise ContractError(
                ErrorCode.UNAUTHORIZED,
                "network transport authentication proof was already used",
                provider_id=self._provider_id,
            )
        unsigned = {key: value for key, value in request.items() if key != "auth"}
        expected = authentication_mac(
            self._authentication_key,
            nonce=nonce,
            issued_at=issued_at_raw,
            request=unsigned,
        )
        if not hmac.compare_digest(mac, expected):
            raise ContractError(
                ErrorCode.UNAUTHORIZED,
                "network transport authentication proof is invalid",
                provider_id=self._provider_id,
            )
        self._seen_auth_nonces[nonce] = now

    def _prune_auth_nonces(self, now: datetime) -> None:
        oldest = now - self._authentication_skew
        self._seen_auth_nonces = {
            nonce: seen_at for nonce, seen_at in self._seen_auth_nonces.items() if seen_at >= oldest
        }


class TcpMessageTransport(MessageTransport):
    """#35 client adapter for a :class:`TcpMessageBroker`."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        ssl_context: ssl.SSLContext | None = None,
        server_hostname: str | None = None,
        authentication_key: str | None = None,
        connect_timeout_seconds: float = 5.0,
        max_frame_bytes: int = DEFAULT_FRAME_BYTES,
        provider_id: str = "tcp-message-transport",
    ) -> None:
        if not host.strip():
            raise ValueError("TCP transport host must not be blank")
        if not 1 <= port <= 65535:
            raise ValueError("TCP transport port must be between 1 and 65535")
        if connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be greater than zero")
        if max_frame_bytes < 1024:
            raise ValueError("max_frame_bytes must be at least 1024")
        if authentication_key is not None and not authentication_key:
            raise ValueError("authentication_key must not be blank when provided")
        if not is_loopback_host(host) and ssl_context is None:
            raise ValueError("non-loopback TCP transport connections require TLS")
        self._host = host
        self._port = port
        self._ssl_context = ssl_context
        self._server_hostname = server_hostname
        self._authentication_key = authentication_key
        self._connect_timeout_seconds = connect_timeout_seconds
        self._max_frame_bytes = max_frame_bytes
        self._provider_id = provider_id
        self._closed = False
        self._available = True
        self._subscriptions: set[_TcpSubscription] = set()
        self._delivery_owners: dict[str, _TcpSubscription] = {}

    @property
    def descriptor(self) -> ProviderDescriptor:
        available = self._available and not self._closed
        return ProviderDescriptor(
            provider_id=self._provider_id,
            provider_type="message_transport",
            supported_operations=(
                "publish",
                "subscribe",
                "ack",
                "nack",
                "dead_letters",
                "close",
            ),
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
                        "cross_process",
                        "cross_host_tls",
                        "nonce_hmac_authentication",
                        "operation_control_timeout",
                        "operation_idempotency_binding",
                    ),
                    limits={"max_frame_bytes": self._max_frame_bytes},
                ),
            ),
            health=HealthStatus.HEALTHY if available else HealthStatus.UNAVAILABLE,
            available=available,
            limits={"max_frame_bytes": self._max_frame_bytes},
        )

    async def check_ready(self) -> bool:
        """Probe broker reachability without changing delivery state."""

        try:
            await self._rpc({"op": "ping"})
        except ContractError:
            return False
        return True

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
        request: dict[str, object] = {
            "op": "publish",
            "topic": topic,
            "envelope": envelope.to_dict(),
        }
        timeout = None if control is None else control.timeout_seconds
        result = await self._rpc(request, timeout_seconds=timeout)
        return decode_receipt(required_mapping(result, "publish receipt"))

    def subscribe(self, subscription: Subscription) -> MessageSubscription:
        self._require_open()
        stream = _TcpSubscription(self, subscription)
        self._subscriptions.add(stream)
        return stream

    async def ack(self, delivery: MessageDelivery) -> None:
        self._require_open()
        owner = self._delivery_owners.get(delivery.metadata.delivery_id)
        if owner is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "delivery is not owned by this TCP transport instance",
                provider_id=self._provider_id,
            )
        await owner.ack(delivery)

    async def nack(
        self,
        delivery: MessageDelivery,
        *,
        retry: bool = True,
        reason: str | None = None,
    ) -> None:
        self._require_open()
        owner = self._delivery_owners.get(delivery.metadata.delivery_id)
        if owner is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "delivery is not owned by this TCP transport instance",
                provider_id=self._provider_id,
            )
        await owner.nack(delivery, retry=retry, reason=reason)

    async def dead_letters(self, topic: str, consumer_group: str) -> tuple[DeadLetter, ...]:
        self._require_open()
        if not topic.strip() or not consumer_group.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "topic and consumer_group must not be blank",
                provider_id=self._provider_id,
            )
        result = await self._rpc(
            {
                "op": "dead_letters",
                "topic": topic,
                "consumer_group": consumer_group,
            }
        )
        if not isinstance(result, list):
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "TCP broker dead-letter response must be an array",
                provider_id=self._provider_id,
            )
        return tuple(decode_dead_letter(as_mapping(item, "dead letter")) for item in result)

    async def close(self, *, graceful: bool = True) -> None:
        del graceful
        if self._closed:
            return
        self._closed = True
        subscriptions = tuple(self._subscriptions)
        for subscription in subscriptions:
            await subscription.aclose()
        self._subscriptions.clear()
        self._delivery_owners.clear()

    async def _rpc(
        self,
        request: dict[str, object],
        *,
        timeout_seconds: float | None = None,
    ) -> object:
        self._require_open()

        async def operation() -> object:
            reader, writer = await self._open_connection()
            try:
                await write_frame(writer, self._authenticated(request))
                response = await read_frame(reader, self._max_frame_bytes)
                if response is None:
                    raise self._unavailable("TCP broker closed the connection without a response")
                return self._result_or_raise(response)
            finally:
                writer.close()
                with suppress(ConnectionError, OSError, ssl.SSLError):
                    await writer.wait_closed()

        try:
            if timeout_seconds is None:
                return await operation()
            async with asyncio.timeout(timeout_seconds):
                return await operation()
        except TimeoutError as exc:
            self._available = False
            raise ContractError(
                ErrorCode.TIMEOUT,
                "network message transport operation timed out",
                retryable=True,
                provider_id=self._provider_id,
            ) from exc

    async def _open_connection(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        self._require_open()
        try:
            async with asyncio.timeout(self._connect_timeout_seconds):
                reader, writer = await asyncio.open_connection(
                    self._host,
                    self._port,
                    ssl=self._ssl_context,
                    server_hostname=(
                        self._server_hostname if self._ssl_context is not None else None
                    ),
                    limit=self._max_frame_bytes + 1,
                )
        except ssl.SSLCertVerificationError as exc:
            self._available = False
            raise ContractError(
                ErrorCode.UNAUTHORIZED,
                "network message transport TLS peer verification failed",
                provider_id=self._provider_id,
            ) from exc
        except (TimeoutError, OSError, ssl.SSLError) as exc:
            self._available = False
            raise self._unavailable("network message broker is unavailable") from exc
        self._available = True
        return reader, writer

    def _authenticated(self, request: Mapping[str, object]) -> dict[str, object]:
        payload = dict(request)
        if self._authentication_key is None:
            return payload
        nonce = uuid4().hex
        issued_at = datetime.now(UTC).isoformat()
        payload["auth"] = {
            "nonce": nonce,
            "issued_at": issued_at,
            "mac": authentication_mac(
                self._authentication_key,
                nonce=nonce,
                issued_at=issued_at,
                request=request,
            ),
        }
        return payload

    def _result_or_raise(self, response: Mapping[str, object]) -> object:
        ok = response.get("ok")
        if ok is True:
            return response.get("result")
        error = required_mapping(response, "error")
        code_raw = required_string(error, "code")
        try:
            code = ErrorCode(code_raw)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                f"TCP broker returned unknown error code: {code_raw}",
                provider_id=self._provider_id,
            ) from exc
        retryable = optional_boolean(error.get("retryable"), "retryable", default=False)
        if code in {ErrorCode.UNAVAILABLE, ErrorCode.TIMEOUT, ErrorCode.TRANSIENT_FAILURE}:
            self._available = False
        raise ContractError(
            code,
            required_string(error, "message"),
            retryable=retryable,
            provider_id=self._provider_id,
        )

    def _register_delivery(self, stream: _TcpSubscription, delivery: MessageDelivery) -> None:
        self._delivery_owners[delivery.metadata.delivery_id] = stream

    def _release_delivery(self, delivery_id: str) -> None:
        self._delivery_owners.pop(delivery_id, None)

    def _release_subscription(self, stream: _TcpSubscription) -> None:
        self._subscriptions.discard(stream)
        stale = [
            delivery_id for delivery_id, owner in self._delivery_owners.items() if owner is stream
        ]
        for delivery_id in stale:
            self._delivery_owners.pop(delivery_id, None)

    def _require_open(self) -> None:
        if self._closed:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "network message transport is closed",
                provider_id=self._provider_id,
            )

    def _unavailable(self, message: str) -> ContractError:
        return ContractError(
            ErrorCode.UNAVAILABLE,
            message,
            retryable=True,
            provider_id=self._provider_id,
        )


class _TcpSubscription(MessageSubscription):
    def __init__(self, transport: TcpMessageTransport, subscription: Subscription) -> None:
        self._transport = transport
        self._subscription = subscription
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._current_delivery_id: str | None = None
        self._closed = False
        self._io_lock = asyncio.Lock()

    def __aiter__(self) -> _TcpSubscription:
        return self

    async def __anext__(self) -> MessageDelivery:
        if self._closed:
            raise StopAsyncIteration
        if self._current_delivery_id is not None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "previous TCP delivery must be acked or nacked before requesting another",
                provider_id=self._transport.descriptor.provider_id,
            )
        last_error: ContractError | None = None
        for attempt in range(_SUBSCRIPTION_RECONNECT_ATTEMPTS):
            async with self._io_lock:
                try:
                    await self._ensure_connected()
                    if self._reader is None:
                        raise self._transport._unavailable(
                            "TCP subscription connection is unavailable"
                        )
                    frame = await read_frame(self._reader, self._transport._max_frame_bytes)
                    if frame is None:
                        raise self._transport._unavailable("TCP subscription connection was closed")
                    if "ok" in frame:
                        self._transport._result_or_raise(frame)
                        raise ContractError(
                            ErrorCode.INVALID_PROVIDER_RESPONSE,
                            "TCP subscription returned a non-delivery response",
                            provider_id=self._transport.descriptor.provider_id,
                        )
                    delivery = decode_delivery(required_mapping(frame, "delivery"))
                except ContractError as exc:
                    last_error = exc
                    await self._reset_connection()
                    retryable = exc.retryable or exc.code is ErrorCode.CONFLICT
                    if not retryable or attempt + 1 >= _SUBSCRIPTION_RECONNECT_ATTEMPTS:
                        raise
                else:
                    self._current_delivery_id = delivery.metadata.delivery_id
                    self._transport._register_delivery(self, delivery)
                    return delivery
            await asyncio.sleep(_SUBSCRIPTION_RECONNECT_DELAY_SECONDS)
        if last_error is not None:
            raise last_error
        raise self._transport._unavailable("TCP subscription could not connect")

    async def ack(self, delivery: MessageDelivery) -> None:
        await self._advance(delivery, {"action": "ack"})

    async def nack(
        self,
        delivery: MessageDelivery,
        *,
        retry: bool,
        reason: str | None,
    ) -> None:
        await self._advance(
            delivery,
            {"action": "nack", "retry": retry, "reason": reason},
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._transport._release_subscription(self)
        self._current_delivery_id = None
        await self._reset_connection()

    async def _advance(self, delivery: MessageDelivery, action: dict[str, object]) -> None:
        if self._closed:
            raise self._transport._unavailable("TCP subscription is closed")
        if self._current_delivery_id != delivery.metadata.delivery_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "delivery is not the current in-flight TCP delivery",
                provider_id=self._transport.descriptor.provider_id,
            )
        async with self._io_lock:
            if self._reader is None or self._writer is None:
                raise self._transport._unavailable("TCP subscription connection is unavailable")
            action["delivery_id"] = delivery.metadata.delivery_id
            try:
                await write_frame(self._writer, action)
                response = await read_frame(self._reader, self._transport._max_frame_bytes)
                if response is None:
                    raise self._transport._unavailable(
                        "TCP broker closed before acknowledging delivery advancement"
                    )
                self._transport._result_or_raise(response)
            except ContractError as exc:
                if exc.code in {
                    ErrorCode.UNAVAILABLE,
                    ErrorCode.TIMEOUT,
                    ErrorCode.TRANSIENT_FAILURE,
                }:
                    self._transport._available = False
                self._transport._release_delivery(delivery.metadata.delivery_id)
                self._current_delivery_id = None
                await self._reset_connection()
                raise
            self._transport._release_delivery(delivery.metadata.delivery_id)
            self._current_delivery_id = None

    async def _ensure_connected(self) -> None:
        if self._reader is not None and self._writer is not None:
            return
        reader, writer = await self._transport._open_connection()
        request: dict[str, object] = {
            "op": "subscribe",
            "subscription": encode_subscription(self._subscription),
        }
        try:
            await write_frame(writer, self._transport._authenticated(request))
            response = await read_frame(reader, self._transport._max_frame_bytes)
            if response is None:
                raise self._transport._unavailable(
                    "TCP broker closed before confirming subscription"
                )
            self._transport._result_or_raise(response)
        except Exception:
            writer.close()
            with suppress(ConnectionError, OSError, ssl.SSLError):
                await writer.wait_closed()
            raise
        self._reader = reader
        self._writer = writer

    async def _reset_connection(self) -> None:
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is not None:
            writer.close()
            with suppress(ConnectionError, OSError, ssl.SSLError):
                await writer.wait_closed()
