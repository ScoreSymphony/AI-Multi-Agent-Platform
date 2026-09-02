# ADR 0005: Separate message transport from canonical event history

- **Status:** Accepted
- **Date:** 2026-09-02
- **Affected issues:** #35, building on #5 and #6
- **Follow-up integrations:** #14, #16, #18, #36, #43

## Context

The platform needs asynchronous communication across components and, later,
processes/nodes. The existing `EventProvider` and #6 kernel already define
canonical Event persistence/history. Reusing that same ownership concept as a
broker abstraction would conflate authoritative history with transient delivery
state and could make the platform depend on one broker's queues, offsets or
retention behavior.

The platform must remain single-node capable while allowing later distributed
brokers to be substituted without changing canonical Task/Run/Event semantics.

## Decision

1. `EventProvider` remains the boundary for canonical Event persistence/history.
2. Introduce a separate platform-owned `MessageTransport` boundary for delivery.
3. Transport uses a versioned `TransportEnvelope`; broker/delivery metadata lives
   in a separate `MessageDelivery.metadata` record.
4. The baseline delivery guarantee is at least once. Duplicate delivery is part
   of the contract and consumers are responsible for idempotency.
5. No exactly-once end-to-end claim is made.
6. Ordering is scoped, not global. The reference implementation guarantees order
   per topic and consumer group.
7. Retry, redelivery, dead-letter and bounded backpressure behavior are explicit.
8. Provide a deterministic bounded in-process transport for single-node use and
   reusable contract tests for future adapters.
9. No permanent broker is selected by this decision.
10. Canonical event messages normally reference canonical event history instead
    of making the transport a second authoritative copy.

## Consequences

### Positive

- Replacing Redis/NATS/Kafka/RabbitMQ or another transport does not redefine
  canonical history.
- Single-node tests and operation require no external broker.
- Delivery failure, retry and duplicate semantics become testable platform
  contracts instead of backend accidents.
- Distributed workers and automation can later share the same envelope and
  correlation/trace fields.
- Broker metadata/IDs remain contained behind adapters.

### Costs and constraints

- Consumers must implement idempotent handling where duplicate side effects
  matter.
- Durable exactly-once side effects, if ever required, need a separate proven
  coordination design across persistence, transport and consumer state.
- Production adapters must document their mapping from platform ordering/group
  semantics to backend partitions/consumer groups.
- The in-process reference implementation is not durable transport storage.

## Alternatives considered

### Treat `EventProvider` as the message broker

Rejected. It would blur canonical history with delivery mechanics and make
broker retention/offset behavior architecture-significant domain state.

### Select one broker now

Rejected. Issue #35 defines the replacement boundary; broker selection depends on
later deployment/scale requirements and must remain an adapter choice.

### Promise exactly-once processing

Rejected. A broker-level exactly-once feature does not prove exactly-once
behavior across canonical persistence and arbitrary consumer side effects.

### Use an unbounded in-memory queue for the reference transport

Rejected. It would hide overload behavior and make tests incapable of enforcing
explicit backpressure semantics.
