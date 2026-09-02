from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, cast
from uuid import uuid4

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id

ENVELOPE_VERSION = "1.0"


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_message_id() -> str:
    return f"message_{uuid4()}"


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(item) for item in value)
    return value


def _freeze_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze_json(item) for key, item in values.items()})


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_thaw_json(item) for item in value]
    return value


def _required_str(data: Mapping[str, Any], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_str(data: Mapping[str, Any], name: str) -> str | None:
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


class MessageKind(StrEnum):
    DOMAIN_EVENT = "domain_event"
    COMMAND = "command"
    NOTIFICATION = "notification"
    SIGNAL = "signal"


class DeliveryGuarantee(StrEnum):
    AT_LEAST_ONCE = "at_least_once"


class OrderingScope(StrEnum):
    TOPIC_CONSUMER_GROUP = "topic_consumer_group"


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str | None = None
    span_id: str | None = None
    trace_flags: str | None = None
    tracestate: str | None = None
    baggage: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "baggage", _freeze_mapping(self.baggage))

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "trace_flags": self.trace_flags,
            "tracestate": self.tracestate,
            "baggage": _thaw_json(self.baggage),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TraceContext:
        baggage_raw = data.get("baggage", {})
        if not isinstance(baggage_raw, Mapping):
            raise ValueError("trace_context.baggage must be an object")
        baggage: dict[str, str] = {}
        for key, value in baggage_raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("trace_context.baggage keys and values must be strings")
            baggage[key] = value
        return cls(
            trace_id=_optional_str(data, "trace_id"),
            span_id=_optional_str(data, "span_id"),
            trace_flags=_optional_str(data, "trace_flags"),
            tracestate=_optional_str(data, "tracestate"),
            baggage=baggage,
        )


@dataclass(frozen=True, slots=True)
class TransportEnvelope:
    message_type: str
    kind: MessageKind
    payload_schema_version: str
    source_component: str
    correlation_id: str
    payload: JsonValue | None = None
    payload_ref: str | None = None
    message_id: str = field(default_factory=new_message_id)
    envelope_version: str = ENVELOPE_VERSION
    timestamp: datetime = field(default_factory=utc_now)
    causation_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    idempotency_key: str | None = None
    trace_context: TraceContext = field(default_factory=TraceContext)
    attributes: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("message_id", self.message_id),
            ("message_type", self.message_type),
            ("payload_schema_version", self.payload_schema_version),
            ("source_component", self.source_component),
            ("correlation_id", self.correlation_id),
            ("envelope_version", self.envelope_version),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be blank")
        if self.envelope_version != ENVELOPE_VERSION:
            raise ValueError(
                f"unsupported envelope_version {self.envelope_version!r}; expected {ENVELOPE_VERSION!r}"
            )
        if (self.payload is None) == (self.payload_ref is None):
            raise ValueError("exactly one of payload or payload_ref must be set")
        if self.payload_ref is not None and not self.payload_ref.strip():
            raise ValueError("payload_ref must not be blank")
        if self.idempotency_key is not None and not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.task_id is not None:
            validate_id(self.task_id, "task")
        if self.run_id is not None:
            validate_id(self.run_id, "run")
        if self.payload is not None:
            object.__setattr__(self, "payload", _freeze_json(self.payload))
        object.__setattr__(self, "attributes", _freeze_mapping(self.attributes))

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible transport-envelope representation."""

        data: dict[str, Any] = {
            "message_id": self.message_id,
            "envelope_version": self.envelope_version,
            "message_type": self.message_type,
            "kind": self.kind.value,
            "payload_schema_version": self.payload_schema_version,
            "timestamp": self.timestamp.astimezone(UTC).isoformat(),
            "source_component": self.source_component,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "idempotency_key": self.idempotency_key,
            "trace_context": self.trace_context.to_dict(),
            "attributes": _thaw_json(self.attributes),
        }
        if self.payload is not None:
            data["payload"] = _thaw_json(self.payload)
        else:
            data["payload_ref"] = self.payload_ref
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TransportEnvelope:
        """Reconstruct an envelope from its canonical JSON-compatible representation."""

        timestamp_raw = _required_str(data, "timestamp")
        try:
            timestamp = datetime.fromisoformat(timestamp_raw)
        except ValueError as exc:
            raise ValueError("timestamp must be an ISO-8601 date-time") from exc
        if timestamp.tzinfo is None:
            raise ValueError("timestamp must include a timezone")

        trace_raw = data.get("trace_context", {})
        if not isinstance(trace_raw, Mapping):
            raise ValueError("trace_context must be an object")
        attributes_raw = data.get("attributes", {})
        if not isinstance(attributes_raw, Mapping):
            raise ValueError("attributes must be an object")

        return cls(
            message_id=_required_str(data, "message_id"),
            envelope_version=_required_str(data, "envelope_version"),
            message_type=_required_str(data, "message_type"),
            kind=MessageKind(_required_str(data, "kind")),
            payload_schema_version=_required_str(data, "payload_schema_version"),
            timestamp=timestamp.astimezone(UTC),
            source_component=_required_str(data, "source_component"),
            correlation_id=_required_str(data, "correlation_id"),
            causation_id=_optional_str(data, "causation_id"),
            project_id=_optional_str(data, "project_id"),
            task_id=_optional_str(data, "task_id"),
            run_id=_optional_str(data, "run_id"),
            idempotency_key=_optional_str(data, "idempotency_key"),
            trace_context=TraceContext.from_dict(trace_raw),
            payload=cast(JsonValue | None, data.get("payload")),
            payload_ref=_optional_str(data, "payload_ref"),
            attributes=cast(Mapping[str, JsonValue], attributes_raw),
        )


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_backoff_seconds: float = 0.0
    backoff_multiplier: float = 2.0
    max_backoff_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.initial_backoff_seconds < 0:
            raise ValueError("initial_backoff_seconds must be >= 0")
        if self.backoff_multiplier < 1:
            raise ValueError("backoff_multiplier must be >= 1")
        if self.max_backoff_seconds < 0:
            raise ValueError("max_backoff_seconds must be >= 0")

    def delay_for_retry(self, completed_attempt: int) -> float:
        if completed_attempt < 1:
            raise ValueError("completed_attempt must be >= 1")
        delay = self.initial_backoff_seconds * (self.backoff_multiplier ** (completed_attempt - 1))
        return min(delay, self.max_backoff_seconds)


@dataclass(frozen=True, slots=True)
class Subscription:
    topic: str
    consumer_id: str
    consumer_group: str = "default"
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    def __post_init__(self) -> None:
        if not self.topic.strip():
            raise ValueError("topic must not be blank")
        if not self.consumer_id.strip():
            raise ValueError("consumer_id must not be blank")
        if not self.consumer_group.strip():
            raise ValueError("consumer_group must not be blank")


@dataclass(frozen=True, slots=True)
class DeliveryMetadata:
    delivery_id: str
    topic: str
    consumer_id: str
    consumer_group: str
    attempt: int
    redelivered: bool
    delivered_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class MessageDelivery:
    envelope: TransportEnvelope
    metadata: DeliveryMetadata


@dataclass(frozen=True, slots=True)
class PublishReceipt:
    message_id: str
    topic: str
    accepted_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class DeadLetter:
    envelope: TransportEnvelope
    topic: str
    consumer_group: str
    attempts: int
    reason: str
    failed_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class TransportSemantics:
    delivery_guarantee: DeliveryGuarantee = DeliveryGuarantee.AT_LEAST_ONCE
    ordering_scope: OrderingScope = OrderingScope.TOPIC_CONSUMER_GROUP
    duplicates_possible: bool = True
    exactly_once_claimed: bool = False
