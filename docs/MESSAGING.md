# Event Transport and Internal Messaging

This document defines the platform-owned message-transport boundary introduced by
Issue #35. It is intentionally broker-neutral and does not select Redis, NATS,
Kafka, RabbitMQ or another permanent transport technology.

## Ownership and terminology

The platform keeps canonical history and delivery mechanics separate.

| Concept | Meaning | Authority |
| --- | --- | --- |
| Domain Event | Canonical statement that something happened in platform state/history | Canonical kernel/event persistence from #6 through `EventProvider` |
| Transport Message | Versioned delivery envelope used to move data between components | `MessageTransport`; never canonical history |
| Command | Request for a component to attempt an action | Becomes authoritative only through resulting canonical state/events |
| Notification / Signal | Non-authoritative communication that may trigger processing | Transport-only unless a canonical state change is later committed |

The source-of-truth rule is therefore:

```text
canonical state / EventProvider
           |
           | creates or references canonical facts
           v
    TransportEnvelope
           |
           v
    MessageTransport
           |
           v
       consumers
```

A broker offset, queue entry, delivery receipt, consumer cursor or dead-letter
record must never replace a canonical Task, Run or Event identity. Deleting or
replacing the transport must not delete or redefine canonical event history.

## Contract surface

`ai_multi_agent_platform.messaging.MessageTransport` is a replaceable,
platform-owned provider boundary. It supports:

- publish;
- subscribe using explicit consumer identity and consumer group;
- acknowledgement and negative acknowledgement;
- bounded retry/redelivery policy;
- dead-letter inspection;
- normalized provider health/capability metadata;
- graceful and forced shutdown;
- canonical `ContractError` failure categories.

The transport extends the common `ProviderContract`, so future distributed
adapters must expose the same normalized descriptor/health boundary as other
platform providers.

The existing `EventProvider` remains the canonical event-history/persistence
boundary. `MessageTransport` does not replace it.

## Versioned transport envelope

`TransportEnvelope` version `1.0` contains delivery-independent message data:

- `message_id`;
- `envelope_version`;
- `message_type`;
- message `kind` (`domain_event`, `command`, `notification`, `signal`);
- `payload_schema_version`;
- UTC timestamp;
- source component;
- correlation ID;
- optional causation ID;
- optional canonical Project, Task and Run IDs;
- optional idempotency/deduplication key;
- trace context (`trace_id`, `span_id`, flags, tracestate and portable baggage);
- exactly one of inline canonical payload or canonical payload reference;
- portable message attributes.

Transport-specific delivery state is deliberately excluded from the envelope.
`MessageDelivery.metadata` carries delivery ID, topic, consumer identity/group,
attempt number, redelivery flag and delivery time. Broker-specific delivery
handles remain adapter-private.

The JSON representation is specified by
`schemas/transport/envelope.v1.schema.json`. `TransportEnvelope.to_dict()` emits
the canonical JSON-compatible wire mapping and `TransportEnvelope.from_dict()`
reconstructs it. The repository validates a full serialize -> JSON Schema ->
deserialize -> serialize round trip. Unsupported envelope versions fail rather
than being accepted silently.

### Canonical domain-event messages

`envelope_for_domain_event(...)` creates an envelope from a canonical `Event`.
By default it uses `canonical-event:<event-id>` as `payload_ref` rather than
copying canonical history into a transport-owned record. It preserves event
correlation, causation, project/run/task identity and trace ID and uses the
canonical event ID as the idempotency key.

A distributed deployment may use another resolvable canonical payload reference,
but transport ownership does not change.

## Delivery semantics

### Baseline guarantee: at least once

The platform transport baseline is **at-least-once delivery**. Duplicate
messages are valid and expected. No end-to-end exactly-once claim is made.

A consumer must therefore be safe when the same message is received more than
once. The recommended key is:

1. `idempotency_key` when present;
2. otherwise `message_id`.

`IdempotentConsumer` and `InMemoryIdempotencyStore` provide a deterministic
single-process helper for tests and simple local consumers. The store coordinates
one in-process owner per key, so concurrently delivered duplicates wait rather
than executing the handler at the same time. If the owner fails or is cancelled,
the claim is released so a waiting duplicate can retry. This is still not a
durable exactly-once mechanism: process loss may erase the in-memory evidence,
and consumers with durable side effects must coordinate durable idempotency state
with those side effects where required.

### Acknowledgement timing

A consumer should acknowledge only after the work represented by the delivery
has reached the consumer's required safe point. For a durable side effect, that
normally means the side effect and its durable idempotency evidence are safely
recorded before `ack`.

A retryable failure uses `nack(..., retry=True)`. A non-retryable rejection may
use `nack(..., retry=False)`. An unacknowledged in-flight delivery is eligible
for redelivery when the consumer disappears/restarts.

### Publish operation control

`MessageTransport.publish(...)` accepts the platform-wide `OperationControl`.
For messaging, its semantics are deliberately narrow:

- `timeout_seconds` bounds the publish operation and maps expiry to canonical
  `ErrorCode.TIMEOUT`;
- when `idempotency_key` is supplied it must match the envelope's own
  `idempotency_key`, preventing two competing idempotency identities at one
  boundary;
- `retry_mode` expresses caller retry intent and does not authorize a transport
  to manufacture an exactly-once guarantee or silently create a second message
  identity.

The reference transport implements timeout enforcement and idempotency-key
binding. Future adapters must preserve these canonical semantics even if their
backend SDK exposes different timeout or retry primitives.

### Retry and poison messages

Each subscription has a `RetryPolicy` containing:

- maximum attempts;
- initial backoff;
- multiplier;
- maximum backoff.

When an attempt is negatively acknowledged and retry is allowed, the same
message is redelivered. Once the maximum attempt count is reached, or retry is
explicitly disabled, the reference transport moves the delivery into a
transport-owned dead-letter record and advances that consumer group's cursor.
Dead letters are diagnostic/delivery state, not canonical domain history.

The reusable contract suite verifies that a configured non-zero retry backoff is
actually observed before redelivery instead of merely checking attempt counters.

## Ordering

The deterministic reference transport guarantees order **per topic and consumer
group**. It permits one in-flight message for a group, so a later message is not
delivered before the current one is acknowledged or dead-lettered.

There is no global ordering guarantee across topics or consumer groups.
Consumers must never rely on accidental broker-global order.

Distributed adapters may internally use broker partitions or routing keys, but
they must document how the configured platform ordering scope maps to the
backend and must not silently promise a stronger global order than the contract.

## Backpressure and overload

The reference transport uses a bounded retained queue (`max_queue_size`).
When the bound is reached, publishing fails explicitly with
`ContractError(ErrorCode.RESOURCE_EXHAUSTED, retryable=True)` rather than
silently dropping a message.

Consequences:

- slow consumers retain messages until their registered groups advance;
- bursts are bounded by configured queue capacity;
- producers can pause/retry according to higher-level policy;
- a topic with no consumers eventually reaches the bound rather than becoming
  an unbounded in-memory buffer;
- retained messages are pruned only after all registered groups no longer need
  them.

Production adapters may provide richer broker-native flow control, but they must
preserve explicit overload/error behavior and must not silently convert overload
into data loss.

## Consumer restart and transport outage

If a reference consumer closes while holding an unacknowledged delivery, its
consumer group retains the cursor and the next consumer in that group receives
the same message as a redelivery with an incremented attempt count.

The reference implementation exposes `set_available(...)` only as a deterministic
test hook. While unavailable, operations fail with retryable
`ErrorCode.UNAVAILABLE`. A production adapter maps backend/network outages into
the same canonical failure category and lets the platform decide when to retry
or reconnect.

The reusable conformance suite receives an adapter-specific availability-toggle
fixture rather than adding outage simulation to the production transport API.

## Shutdown

Graceful shutdown:

1. stops accepting new publishes;
2. enters draining mode;
3. allows consumers to stay connected or reconnect so retained deliveries can drain;
4. closes once retained messages and in-flight deliveries are drained.

If no consumer is available for retained work, graceful close remains in draining
mode instead of discarding messages. A caller may later reconnect a consumer or
choose forced shutdown explicitly.

Forced shutdown may abandon pending **transport** deliveries. It never mutates
canonical state or canonical event history.

## Security hooks

Issue #35 establishes the transport boundary; authentication/authorization and
remote-transport hardening remain follow-up work. Every distributed adapter must
be able to add, without changing the canonical envelope ownership model:

- authenticated publisher and consumer/service identities;
- authorization by topic/message class/project scope;
- encrypted remote transport;
- payload minimization and redaction;
- prohibition on plaintext secrets in messages;
- replay protection where required;
- envelope integrity/authenticity controls where required;
- audit/telemetry correlation using the existing IDs/trace context.

Secrets should be passed by secure reference where possible rather than copied
into message payloads.

## Distributed adapter guidance

Future Redis, NATS, Kafka, RabbitMQ or other adapters implement the same
`MessageTransport` contract. A broker is an implementation choice, not a new
canonical layer.

Adapters must:

1. preserve the `TransportEnvelope` fields and version;
2. keep broker delivery metadata outside canonical payload/domain objects;
3. map platform consumer identity/group semantics onto broker consumers;
4. implement acknowledgement/redelivery/dead-letter behavior without claiming
   exactly-once unless the complete end-to-end chain proves it;
5. document ordering-key/partition behavior;
6. expose bounded capacity/backpressure behavior;
7. map backend exceptions to canonical `ContractError` categories;
8. expose normalized health/capabilities;
9. keep broker topics, offsets, message IDs and internal handles out of canonical
   domain identities;
10. preserve correlation, causation and trace context exactly;
11. support clean cancellation/shutdown/reconnection behavior;
12. preserve the canonical `OperationControl` timeout/idempotency-key binding.

The broad existing `CapabilityKind.EVENT` is used for discovery metadata by the
reference implementation, while `provider_type="message_transport"` and
capability name `message_transport` distinguish this delivery provider from the
canonical `EventProvider`. The capability tag does not transfer event-history
ownership to the message transport.

## Reference implementation

`InProcessMessageTransport` is the deterministic single-node implementation used
for platform development and contract tests. It intentionally has no external
dependency and provides:

- bounded in-memory topics;
- explicit consumer IDs/groups;
- at-least-once redelivery;
- per-topic/per-group ordering;
- retry/backoff;
- dead-letter handling;
- outage simulation;
- publish timeout and operation/envelope idempotency-key binding;
- graceful/forced shutdown;
- normalized provider metadata.

It is not a durable broker and its retained transport queue is intentionally not
recoverable canonical history.

## Reusable contract tests

`MessageTransportContractSuite` is pytest-independent and can be run against a
configured future adapter. Full compliance checks:

- normalized transport descriptor/capabilities;
- publish/subscribe and full correlation/causation/trace metadata preservation;
- operation/envelope idempotency-key binding;
- duplicate redelivery and scoped ordering;
- idempotent-consumer behavior;
- configured retry backoff;
- consumer restart behavior;
- retry exhaustion/dead-letter behavior;
- canonical transport-unavailable behavior;
- bounded backpressure behavior;
- graceful shutdown.

Future adapters provide three test fixtures: their normal transport factory, a
deliberately bounded transport factory and an availability toggle. This keeps
backend-specific failure injection out of the production `MessageTransport`
interface while still making outage and overload behavior part of reusable
contract compliance.

The repository adds reference-specific regression tests for publish timeout,
concurrent duplicate-handler serialization, JSON Schema wire round trips,
version rejection and canonical domain-event references.

## Non-goals

This architecture does not:

- select a permanent broker;
- make transport storage canonical history;
- replace #6 event persistence;
- implement worker scheduling (#14);
- implement automation triggers (#18);
- implement final service authentication (#36);
- claim exactly-once processing.

Distributed scheduling, full distributed tracing, automation, authenticated
service identities and transport-security hardening integrate later through #14,
#16, #18, #36 and #43 without changing this ownership split.
