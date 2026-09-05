"""Dependency-free cross-process MessageTransport adapter for issue #388.

The adapter is intentionally a transport implementation, never a canonical state
or identity layer.  A small TCP broker owns only delivery state and delegates
ordering/retry/dead-letter semantics to the existing deterministic #35 reference
transport.  Remote listeners require TLS; an optional runtime-only pre-shared
credential is proved with a nonce-bound HMAC and is never copied into a
TransportEnvelope or sent as plaintext.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import ssl
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import cast
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
from ai_multi_agent_platform.contracts.types import JsonValue

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
from .reference import InProcessMessageTransport

_DEFAULT_FRAME_BYTES = 4 * 1024 * 1024
_DEFAULT_AUTH_SKEW = timedelta(minutes=5)


class TcpMessageBroker:
    """Small self-hosted broker exposing #35 semantics over asyncio TCP.

    The broker is deliberately not durable canonical history.  Restarting it may
    lose transport-only retained deliveries exactly like replacing any other
    non-durable MessageTransport backend; canonical Task/Run/Event state remains
    elsewhere.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        max_queue_size: int = 1024,
        max_frame_bytes: int = _DEFAULT_FRAME_BYTES,
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
        if not _is_loopback_host(host):
            if ssl_context is None:
                raise ValueError("non-loopback TCP broker listeners require TLS")
            if authentication_key is None and ssl_context.verify_mode is not ssl.CERT_REQUIRED:
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
        assert self._server is not None
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
            for writer in tuple(self._connections):
                writer.close()
            if self._connections:
                await asyncio.gather(
                    *(writer.wait_closed() for writer in tuple(self._connections)),
                    return_exceptions=True,
                )

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._connections.add(writer)
        try:
            request = await _read_frame(reader, self._max_frame_bytes)
            if request is None:
                return
            self._authenticate(request, writer)
            operation = _required_string(request, "op")
            if operation == "subscribe":
                await self._serve_subscription(request, reader, writer)
                return
            response = await self._handle_request(operation, request)
            await _write_frame(writer, {"ok": True, "result": response})
        except ContractError as exc:
            await _try_write_error(writer, exc)
        except Exception:
            await _try_write_error(
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
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError, ssl.SSLError):
                pass

    async def _handle_request(
        self,
        operation: str,
        request: Mapping[str, object],
    ) -> JsonValue:
        if operation == "ping":
            return {"provider_id": self._provider_id}
        if operation == "publish":
            topic = _required_string(request, "topic")
            envelope = TransportEnvelope.from_dict(_required_mapping(request, "envelope"))
            receipt = await self._backend.publish(topic, envelope)
            return _encode_receipt(receipt)
        if operation == "dead_letters":
            topic = _required_string(request, "topic")
            consumer_group = _required_string(request, "consumer_group")
            letters = await self._backend.dead_letters(topic, consumer_group)
            return [_encode_dead_letter(item) for item in letters]
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
        subscription = _decode_subscription(_required_mapping(request, "subscription"))
        stream = self._backend.subscribe(subscription)
        await _write_frame(writer, {"ok": True, "result": {"subscribed": True}})
        try:
            async for delivery in stream:
                await _write_frame(writer, {"delivery": _encode_delivery(delivery)})
                action = await _read_frame(reader, self._max_frame_bytes)
                if action is None:
                    return
                action_name = _required_string(action, "action")
                delivery_id = _required_string(action, "delivery_id")
                if delivery_id != delivery.metadata.delivery_id:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "delivery acknowledgement does not match current delivery",
                        provider_id=self._provider_id,
                    )
                if action_name == "ack":
                    await self._backend.ack(delivery)
                elif action_name == "nack":
                    retry = _optional_boolean(action.get("retry"), "retry", default=True)
                    reason = _optional_string(action.get("reason"), "reason")
                    await self._backend.nack(delivery, retry=retry, reason=reason)
                else:
                    raise ContractError(
                        ErrorCode.INVALID_REQUEST,
                        f"unsupported subscription action: {action_name}",
                        provider_id=self._provider_id,
                    )
                await _write_frame(writer, {"ok": True, "result": {"advanced": True}})
        finally:
            await stream.aclose()

    def _authenticate(self, request: Mapping[str, object], writer: asyncio.StreamWriter) -> None:
        if self._authentication_key is None:
            if _is_loopback_host(self._host):
                return
            ssl_object = writer.get_extra_info("ssl_object")
            if not isinstance(ssl_object, ssl.SSLObject) or not ssl_object.getpeercert():
                raise ContractError(
                    ErrorCode.UNAUTHORIZED,
                    "network transport client identity is required",
                    provider_id=self._provider_id,
                )
            return

        auth = _required_mapping(request, "auth")
        nonce = _required_string(auth, "nonce")
        issued_at_raw = _required_string(auth, "issued_at")
        mac = _required_string(auth, "mac")
        issued_at = _parse_datetime(issued_at_raw, "auth.issued_at")
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
        expected = _authentication_mac(
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
            nonce: seen_at
            for nonce, seen_at in self._seen_auth_nonces.items()
            if seen_at >= oldest
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
        max_frame_bytes: int = _DEFAULT_FRAME_BYTES,
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
        if not _is_loopback_host(host) and ssl_context is None:
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
        """Probe broker reachability without changing transport-owned delivery state."""

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
        return _decode_receipt(_required_mapping(result, "publish receipt"))

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
        return tuple(_decode_dead_letter(_as_mapping(item, "dead letter")) for item in result)

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
                await _write_frame(writer, self._authenticated(request))
                response = await _read_frame(reader, self._max_frame_bytes)
                if response is None:
                    raise self._unavailable("TCP broker closed the connection without a response")
                return self._result_or_raise(response)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError, ssl.SSLError):
                    pass

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
                        self._server_hostname
                        if self._ssl_context is not None
                        else None
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
            "mac": _authentication_mac(
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
        error = _required_mapping(response, "error")
        code_raw = _required_string(error, "code")
        try:
            code = ErrorCode(code_raw)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                f"TCP broker returned unknown error code: {code_raw}",
                provider_id=self._provider_id,
            ) from exc
        retryable = _optional_boolean(error.get("retryable"), "retryable", default=False)
        if code in {ErrorCode.UNAVAILABLE, ErrorCode.TIMEOUT, ErrorCode.TRANSIENT_FAILURE}:
            self._available = False
        raise ContractError(
            code,
            _required_string(error, "message"),
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
            delivery_id
            for delivery_id, owner in self._delivery_owners.items()
            if owner is stream
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
        async with self._io_lock:
            await self._ensure_connected()
            assert self._reader is not None
            try:
                frame = await _read_frame(self._reader, self._transport._max_frame_bytes)
            except ContractError:
                await self._reset_connection()
                raise
            if frame is None:
                await self._reset_connection()
                self._transport._available = False
                raise self._transport._unavailable("TCP subscription connection was closed")
            if "ok" in frame:
                self._transport._result_or_raise(frame)
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "TCP subscription returned a response where a delivery was expected",
                    provider_id=self._transport.descriptor.provider_id,
                )
            delivery = _decode_delivery(_required_mapping(frame, "delivery"))
            self._current_delivery_id = delivery.metadata.delivery_id
            self._transport._register_delivery(self, delivery)
            return delivery

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
            {
                "action": "nack",
                "retry": retry,
                "reason": reason,
            },
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
                await _write_frame(self._writer, action)
                response = await _read_frame(
                    self._reader,
                    self._transport._max_frame_bytes,
                )
                if response is None:
                    raise self._transport._unavailable(
                        "TCP broker closed before acknowledging delivery advancement"
                    )
                self._transport._result_or_raise(response)
            except (ContractError, ConnectionError, OSError, ssl.SSLError):
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
            "subscription": _encode_subscription(self._subscription),
        }
        try:
            await _write_frame(writer, self._transport._authenticated(request))
            response = await _read_frame(reader, self._transport._max_frame_bytes)
            if response is None:
                raise self._transport._unavailable(
                    "TCP broker closed before confirming subscription"
                )
            self._transport._result_or_raise(response)
        except Exception:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError, ssl.SSLError):
                pass
            raise
        self._reader = reader
        self._writer = writer

    async def _reset_connection(self) -> None:
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError, ssl.SSLError):
                pass


async def _read_frame(
    reader: asyncio.StreamReader,
    max_frame_bytes: int,
) -> dict[str, object] | None:
    try:
        raw = await reader.readline()
    except (ValueError, asyncio.LimitOverrunError) as exc:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "network transport frame exceeded configured limit",
        ) from exc
    if not raw:
        return None
    if len(raw) > max_frame_bytes:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "network transport frame exceeded configured limit",
        )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "network transport frame is not valid UTF-8 JSON",
        ) from exc
    return _as_mapping(value, "network transport frame")


async def _write_frame(writer: asyncio.StreamWriter, value: Mapping[str, object]) -> None:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "network transport frame is not JSON serializable",
        ) from exc
    writer.write(encoded)
    try:
        await writer.drain()
    except (ConnectionError, OSError, ssl.SSLError) as exc:
        raise ContractError(
            ErrorCode.UNAVAILABLE,
            "network transport connection is unavailable",
            retryable=True,
        ) from exc


async def _try_write_error(writer: asyncio.StreamWriter, error: ContractError) -> None:
    if writer.is_closing():
        return
    try:
        await _write_frame(
            writer,
            {
                "ok": False,
                "error": {
                    "code": error.code.value,
                    "message": error.message,
                    "retryable": error.retryable,
                },
            },
        )
    except ContractError:
        pass


def _authentication_mac(
    key: str,
    *,
    nonce: str,
    issued_at: str,
    request: Mapping[str, object],
) -> str:
    canonical = json.dumps(
        request,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    signed = nonce.encode("utf-8") + b"\n" + issued_at.encode("utf-8") + b"\n" + canonical
    return hmac.new(key.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _encode_subscription(subscription: Subscription) -> dict[str, object]:
    return {
        "topic": subscription.topic,
        "consumer_id": subscription.consumer_id,
        "consumer_group": subscription.consumer_group,
        "retry_policy": {
            "max_attempts": subscription.retry_policy.max_attempts,
            "initial_backoff_seconds": subscription.retry_policy.initial_backoff_seconds,
            "backoff_multiplier": subscription.retry_policy.backoff_multiplier,
            "max_backoff_seconds": subscription.retry_policy.max_backoff_seconds,
        },
    }


def _decode_subscription(data: Mapping[str, object]) -> Subscription:
    retry = _required_mapping(data, "retry_policy")
    return Subscription(
        topic=_required_string(data, "topic"),
        consumer_id=_required_string(data, "consumer_id"),
        consumer_group=_required_string(data, "consumer_group"),
        retry_policy=RetryPolicy(
            max_attempts=_required_int(retry, "max_attempts"),
            initial_backoff_seconds=_required_float(retry, "initial_backoff_seconds"),
            backoff_multiplier=_required_float(retry, "backoff_multiplier"),
            max_backoff_seconds=_required_float(retry, "max_backoff_seconds"),
        ),
    )


def _encode_delivery(delivery: MessageDelivery) -> dict[str, object]:
    return {
        "envelope": delivery.envelope.to_dict(),
        "metadata": {
            "delivery_id": delivery.metadata.delivery_id,
            "topic": delivery.metadata.topic,
            "consumer_id": delivery.metadata.consumer_id,
            "consumer_group": delivery.metadata.consumer_group,
            "attempt": delivery.metadata.attempt,
            "redelivered": delivery.metadata.redelivered,
            "delivered_at": delivery.metadata.delivered_at.astimezone(UTC).isoformat(),
        },
    }


def _decode_delivery(data: Mapping[str, object]) -> MessageDelivery:
    metadata = _required_mapping(data, "metadata")
    return MessageDelivery(
        envelope=TransportEnvelope.from_dict(_required_mapping(data, "envelope")),
        metadata=DeliveryMetadata(
            delivery_id=_required_string(metadata, "delivery_id"),
            topic=_required_string(metadata, "topic"),
            consumer_id=_required_string(metadata, "consumer_id"),
            consumer_group=_required_string(metadata, "consumer_group"),
            attempt=_required_int(metadata, "attempt"),
            redelivered=_required_boolean(metadata, "redelivered"),
            delivered_at=_parse_datetime(
                _required_string(metadata, "delivered_at"),
                "delivered_at",
            ),
        ),
    )


def _encode_receipt(receipt: PublishReceipt) -> dict[str, object]:
    return {
        "message_id": receipt.message_id,
        "topic": receipt.topic,
        "accepted_at": receipt.accepted_at.astimezone(UTC).isoformat(),
    }


def _decode_receipt(data: Mapping[str, object]) -> PublishReceipt:
    return PublishReceipt(
        message_id=_required_string(data, "message_id"),
        topic=_required_string(data, "topic"),
        accepted_at=_parse_datetime(
            _required_string(data, "accepted_at"),
            "accepted_at",
        ),
    )


def _encode_dead_letter(letter: DeadLetter) -> dict[str, object]:
    return {
        "envelope": letter.envelope.to_dict(),
        "topic": letter.topic,
        "consumer_group": letter.consumer_group,
        "attempts": letter.attempts,
        "reason": letter.reason,
        "failed_at": letter.failed_at.astimezone(UTC).isoformat(),
    }


def _decode_dead_letter(data: Mapping[str, object]) -> DeadLetter:
    return DeadLetter(
        envelope=TransportEnvelope.from_dict(_required_mapping(data, "envelope")),
        topic=_required_string(data, "topic"),
        consumer_group=_required_string(data, "consumer_group"),
        attempts=_required_int(data, "attempts"),
        reason=_required_string(data, "reason"),
        failed_at=_parse_datetime(_required_string(data, "failed_at"), "failed_at"),
    )


def _as_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            f"{label} must be an object",
        )
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                f"{label} keys must be strings",
            )
        result[key] = cast(object, item)
    return result


def _required_mapping(data: Mapping[str, object], name: str) -> dict[str, object]:
    if name in data:
        return _as_mapping(data[name], name)
    return _as_mapping(data, name)


def _required_string(data: Mapping[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be a non-blank string")
    return value


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be a string or null")
    return value


def _required_int(data: Mapping[str, object], name: str) -> int:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be an integer")
    return value


def _required_float(data: Mapping[str, object], name: str) -> float:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be numeric")
    return float(value)


def _required_boolean(data: Mapping[str, object], name: str) -> bool:
    value = data.get(name)
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be a boolean")
    return value


def _optional_boolean(value: object, name: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be a boolean")
    return value


def _parse_datetime(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must be an ISO-8601 date-time",
        ) from exc
    if parsed.tzinfo is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must include a timezone",
        )
    return parsed.astimezone(UTC)
