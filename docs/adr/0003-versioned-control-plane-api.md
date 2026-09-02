# ADR 0003: Version the northbound Control Plane independently of adapters

- **Status:** Accepted
- **Date:** 2026-09-02
- **Issue:** #32

## Context

The platform has replaceable orchestration, execution, model, tool, event, authorization, data, node and worker boundaries. Web, CLI, automation and external clients need one stable interface even when those implementations change.

Direct client use of Hermes, Forge, model-provider, MCP, storage or worker APIs would turn backend-specific schemas and lifecycle semantics into accidental platform contracts.

## Decision

The platform owns one versioned northbound Control Plane.

The first major API is `/api/v1`. It exposes canonical platform resources, explicit lifecycle/administrative commands, one error envelope, uniform query conventions, health/readiness data and canonical-event live updates.

The application service remains framework-independent. HTTP/ASGI is a replaceable transport. Backend-private types, handles, adapter metadata and exceptions must be translated or rejected before crossing the northbound boundary.

Domains with an existing canonical application service are delegated to directly. Later or independently developed domains integrate through platform-owned `ResourceService` and `CommandHandler` seams. Missing integrations return canonical `unavailable`; the Control Plane never falls back to proxying a private backend API.

Major API versioning is independent from adapter/upstream versions:

- additive compatible changes stay within a major version;
- breaking canonical contract changes require a new major namespace;
- deprecations overlap with their replacement for a documented migration window;
- unsupported versions return an explicit canonical API error.

Authentication and authorization remain replaceable boundaries. The Control Plane propagates actor, correlation and idempotency context and invokes `AuthorizationProvider` without defining a second policy model.

## Consequences

### Positive

- Frontend, CLI, automations and external clients share one contract.
- Hermes, Forge, MCP, storage engines, model gateways and workers remain replaceable.
- Independent feature waves can attach to the same API without importing each other's concrete implementations.
- API compatibility and backend-private type leakage can be contract-tested.
- Live updates carry canonical Event data.

### Costs

- The Control Plane maintains serialization and translation code.
- New canonical domains need a ResourceService/CommandHandler integration before clients can use them.
- Multiple API majors may need temporary parallel support during breaking migrations.

## Alternatives considered

### Expose backend APIs directly

Rejected because it couples clients to implementation details and violates the replaceable-adapter architecture.

### Pre-bind the Control Plane to one web framework

Rejected because framework request/response objects would become architecture-level contracts.

### Let each subsystem publish its own public API

Rejected because clients would need concrete service topology and backend knowledge, defeating the platform-wide northbound boundary.

## Affected contracts/issues

- #32 Platform API / Control Plane
- #12 capability/tool integration
- #13 persistence/files/memory/knowledge
- #15 authorization boundary
- #16 observability
- #17 frontend
- #38 CLI
- canonical domain model, provider contracts and Task/Run/Event kernel
