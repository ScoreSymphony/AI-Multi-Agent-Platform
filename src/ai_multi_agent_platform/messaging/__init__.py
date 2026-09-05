"""Replaceable internal message transport contracts and reference implementations."""

from .conformance import MessageTransportContractSuite
from .contracts import MessageSubscription, MessageTransport
from .helpers import IdempotentConsumer, InMemoryIdempotencyStore, envelope_for_domain_event
from .models import (
    ENVELOPE_VERSION,
    DeadLetter,
    DeliveryGuarantee,
    DeliveryMetadata,
    MessageDelivery,
    MessageKind,
    OrderingScope,
    PublishReceipt,
    RetryPolicy,
    Subscription,
    TraceContext,
    TransportEnvelope,
    TransportSemantics,
)
from .network import TcpMessageBroker, TcpMessageTransport
from .reference import InProcessMessageTransport

__all__ = [
    "ENVELOPE_VERSION",
    "DeadLetter",
    "DeliveryGuarantee",
    "DeliveryMetadata",
    "IdempotentConsumer",
    "InMemoryIdempotencyStore",
    "InProcessMessageTransport",
    "MessageDelivery",
    "MessageKind",
    "MessageSubscription",
    "MessageTransport",
    "MessageTransportContractSuite",
    "OrderingScope",
    "PublishReceipt",
    "RetryPolicy",
    "Subscription",
    "TcpMessageBroker",
    "TcpMessageTransport",
    "TraceContext",
    "TransportEnvelope",
    "TransportSemantics",
    "envelope_for_domain_event",
]
