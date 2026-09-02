# Issue #35 completion evidence

Issue #35 defines a broker-neutral event-transport/internal-messaging boundary
without changing canonical event ownership established by #6.

## Delivered

- `ai_multi_agent_platform.messaging.MessageTransport` replaceable contract;
- versioned `TransportEnvelope` and JSON Schema;
- canonical `TransportEnvelope.to_dict()` / `from_dict()` wire representation;
- separate delivery metadata;
- deterministic bounded `InProcessMessageTransport`;
- at-least-once duplicate/redelivery semantics;
- explicit consumer identity/group, acknowledgement and negative acknowledgement;
- retry/backoff and dead-letter handling;
- bounded backpressure and normalized outage errors;
- graceful/forced shutdown semantics;
- `OperationControl.timeout_seconds` enforcement for reference publishes;
- binding between operation and envelope idempotency keys without an exactly-once claim;
- domain-event envelope helper preserving correlation/causation/trace fields;
- concurrency-safe process-local idempotent-consumer helper;
- reusable `MessageTransportContractSuite` including outage/backpressure fixtures;
- architecture documentation and ADR 0005;
- distributed-adapter and security-hook guidance.

## Acceptance mapping

| Acceptance criterion | Evidence |
| --- | --- |
| Domain Events and transport messages differ | `docs/MESSAGING.md`, ADR 0005 |
| Versioned canonical envelope | `TransportEnvelope`, `envelope.v1.schema.json`, schema-validating round-trip test |
| Replaceable implementation | `MessageTransport` extends platform `ProviderContract` |
| Single-node operation | `InProcessMessageTransport` |
| Duplicate/idempotent semantics | at-least-once contract + sequential/concurrent `IdempotentConsumer` tests |
| Scoped ordering | topic + consumer-group ordering contract/tests |
| Retry/dead-letter/backpressure | reusable conformance suite + reference implementation tests |
| Correlation/causation/trace preserved | full trace-context conformance + domain-event mapping tests |
| Canonical history independent of transport | `EventProvider` remains canonical; ADR 0005 |
| Later distributed/security/automation integration | documented adapter/security hooks |

## Required test coverage

`tests/test_message_transport.py` and `MessageTransportContractSuite` cover:

- publish/subscribe success;
- duplicate delivery and idempotent consumer handling;
- concurrent duplicate-handler serialization;
- consumer restart/redelivery;
- failed delivery retry and configured backoff;
- poison/dead-letter behavior;
- defined-scope ordering;
- transport-unavailable behavior;
- bounded backpressure;
- graceful shutdown;
- correlation/causation/full trace-context preservation;
- `OperationControl` idempotency-key binding;
- `OperationControl` publish timeout enforcement on the reference transport;
- canonical domain-event reference mapping;
- envelope JSON Schema validation and wire round trip;
- reference implementation full conformance-suite compliance.

The reusable conformance suite is intentionally independent from pytest. Future
broker adapters supply their normal factory, a deliberately bounded factory and
an availability-toggle fixture so outage and backpressure semantics are tested
without adding test-only controls to the production `MessageTransport` interface.

## Exactly-once boundary

The hardening does not introduce an exactly-once claim. The in-memory
idempotency helper prevents concurrent duplicate handler execution inside one
process, but durable consumers must still coordinate durable idempotency evidence
with their side effects. Process loss may still cause a legitimate at-least-once
redelivery.

## Explicitly deferred

This issue does not select a permanent broker, implement distributed worker
scheduling, implement automation triggers, make transport storage canonical or
claim exactly-once semantics. Those remain downstream concerns as specified by
#35.
