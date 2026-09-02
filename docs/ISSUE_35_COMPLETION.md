# Issue #35 completion evidence

Issue #35 defines a broker-neutral event-transport/internal-messaging boundary
without changing canonical event ownership established by #6.

## Delivered

- `ai_multi_agent_platform.messaging.MessageTransport` replaceable contract;
- versioned `TransportEnvelope` and JSON Schema;
- separate delivery metadata;
- deterministic bounded `InProcessMessageTransport`;
- at-least-once duplicate/redelivery semantics;
- explicit consumer identity/group, acknowledgement and negative acknowledgement;
- retry/backoff and dead-letter handling;
- bounded backpressure and normalized outage errors;
- graceful/forced shutdown semantics;
- domain-event envelope helper preserving correlation/causation/trace fields;
- idempotent-consumer helper without an exactly-once claim;
- reusable `MessageTransportContractSuite`;
- architecture documentation and ADR 0005;
- distributed-adapter and security-hook guidance.

## Acceptance mapping

| Acceptance criterion | Evidence |
| --- | --- |
| Domain Events and transport messages differ | `docs/MESSAGING.md`, ADR 0005 |
| Versioned canonical envelope | `TransportEnvelope`, `envelope.v1.schema.json` |
| Replaceable implementation | `MessageTransport` extends platform `ProviderContract` |
| Single-node operation | `InProcessMessageTransport` |
| Duplicate/idempotent semantics | at-least-once contract + `IdempotentConsumer` tests |
| Scoped ordering | topic + consumer-group ordering contract/tests |
| Retry/dead-letter/backpressure | reference implementation and tests |
| Correlation/causation/trace preserved | envelope model/domain-event mapping tests |
| Canonical history independent of transport | `EventProvider` remains canonical; ADR 0005 |
| Later distributed/security/automation integration | documented adapter/security hooks |

## Required test coverage

`tests/test_message_transport.py` covers:

- publish/subscribe success;
- duplicate delivery and idempotent consumer handling;
- consumer restart/redelivery;
- failed delivery retry;
- poison/dead-letter behavior;
- defined-scope ordering;
- transport-unavailable behavior;
- bounded backpressure;
- graceful shutdown;
- correlation/causation/trace preservation;
- canonical domain-event reference mapping;
- reference implementation conformance-suite compliance.

The reusable conformance suite is intentionally independent from pytest so future
broker adapters can run it with their own configured transport factory.

## Explicitly deferred

This issue does not select a permanent broker, implement distributed worker
scheduling, implement automation triggers, make transport storage canonical or
claim exactly-once semantics. Those remain downstream concerns as specified by
#35.
