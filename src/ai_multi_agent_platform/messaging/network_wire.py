"""Private JSON-line wire helpers for the #388 TCP MessageTransport adapter."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import ssl
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .models import (
    DeadLetter,
    DeliveryMetadata,
    MessageDelivery,
    PublishReceipt,
    RetryPolicy,
    Subscription,
    TransportEnvelope,
)

DEFAULT_FRAME_BYTES = 4 * 1024 * 1024


async def read_frame(
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
    return as_mapping(value, "network transport frame")


async def write_frame(writer: asyncio.StreamWriter, value: Mapping[str, object]) -> None:
    try:
        encoded = (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            + b"\n"
        )
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


async def try_write_error(writer: asyncio.StreamWriter, error: ContractError) -> None:
    if writer.is_closing():
        return
    try:
        await write_frame(
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


def authentication_mac(
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
    signed = nonce.encode() + b"\n" + issued_at.encode() + b"\n" + canonical
    return hmac.new(key.encode(), signed, hashlib.sha256).hexdigest()


def is_loopback_host(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def encode_subscription(subscription: Subscription) -> dict[str, object]:
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


def decode_subscription(data: Mapping[str, object]) -> Subscription:
    retry = required_mapping(data, "retry_policy")
    return Subscription(
        topic=required_string(data, "topic"),
        consumer_id=required_string(data, "consumer_id"),
        consumer_group=required_string(data, "consumer_group"),
        retry_policy=RetryPolicy(
            max_attempts=required_int(retry, "max_attempts"),
            initial_backoff_seconds=required_float(retry, "initial_backoff_seconds"),
            backoff_multiplier=required_float(retry, "backoff_multiplier"),
            max_backoff_seconds=required_float(retry, "max_backoff_seconds"),
        ),
    )


def encode_delivery(delivery: MessageDelivery) -> dict[str, object]:
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


def decode_delivery(data: Mapping[str, object]) -> MessageDelivery:
    metadata = required_mapping(data, "metadata")
    return MessageDelivery(
        envelope=TransportEnvelope.from_dict(required_mapping(data, "envelope")),
        metadata=DeliveryMetadata(
            delivery_id=required_string(metadata, "delivery_id"),
            topic=required_string(metadata, "topic"),
            consumer_id=required_string(metadata, "consumer_id"),
            consumer_group=required_string(metadata, "consumer_group"),
            attempt=required_int(metadata, "attempt"),
            redelivered=required_boolean(metadata, "redelivered"),
            delivered_at=parse_datetime(
                required_string(metadata, "delivered_at"),
                "delivered_at",
            ),
        ),
    )


def encode_receipt(receipt: PublishReceipt) -> dict[str, object]:
    return {
        "message_id": receipt.message_id,
        "topic": receipt.topic,
        "accepted_at": receipt.accepted_at.astimezone(UTC).isoformat(),
    }


def decode_receipt(data: Mapping[str, object]) -> PublishReceipt:
    return PublishReceipt(
        message_id=required_string(data, "message_id"),
        topic=required_string(data, "topic"),
        accepted_at=parse_datetime(
            required_string(data, "accepted_at"),
            "accepted_at",
        ),
    )


def encode_dead_letter(letter: DeadLetter) -> dict[str, object]:
    return {
        "envelope": letter.envelope.to_dict(),
        "topic": letter.topic,
        "consumer_group": letter.consumer_group,
        "attempts": letter.attempts,
        "reason": letter.reason,
        "failed_at": letter.failed_at.astimezone(UTC).isoformat(),
    }


def decode_dead_letter(data: Mapping[str, object]) -> DeadLetter:
    return DeadLetter(
        envelope=TransportEnvelope.from_dict(required_mapping(data, "envelope")),
        topic=required_string(data, "topic"),
        consumer_group=required_string(data, "consumer_group"),
        attempts=required_int(data, "attempts"),
        reason=required_string(data, "reason"),
        failed_at=parse_datetime(required_string(data, "failed_at"), "failed_at"),
    )


def as_mapping(value: object, label: str) -> dict[str, object]:
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


def required_mapping(data: object, name: str) -> dict[str, object]:
    if isinstance(data, Mapping) and name in data:
        return as_mapping(data[name], name)
    return as_mapping(data, name)


def required_string(data: Mapping[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be a non-blank string")
    return value


def optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be a string or null")
    return value


def required_int(data: Mapping[str, object], name: str) -> int:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be an integer")
    return value


def required_float(data: Mapping[str, object], name: str) -> float:
    value = data.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be numeric")
    return float(value)


def required_boolean(data: Mapping[str, object], name: str) -> bool:
    value = data.get(name)
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be a boolean")
    return value


def optional_boolean(value: object, name: str, *, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{name} must be a boolean")
    return value


def parse_datetime(value: str, name: str) -> datetime:
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
