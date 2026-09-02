from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
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
