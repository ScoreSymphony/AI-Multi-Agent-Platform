# Core Provider Contracts

This document defines the rules for replaceable provider interfaces in the AI Multi-Agent Platform.

The contracts are owned by the platform. Upstream systems implement adapters behind them; they do not define the contracts themselves.

## Contract families

The initial contract surface contains:

| Contract | Responsibility |
| --- | --- |
| `CapabilityProvider` | Aggregate and filter normalized capabilities from one registry/source |
| `Orchestrator` | Produce a provider-neutral plan for canonical task intent |
| `LifecycleBackend` | Start, observe and cancel canonical run attempts |
| `ModelProvider` | Execute one provider-neutral model request |
| `ModelRouter` | Select a model provider from neutral requirements |
| `ToolProvider` | Invoke a tool through a normalized request/result boundary |
| `MemoryProvider` | Store and retrieve memory entries |
| `FileProvider` | Store/read payload bytes for canonical object references |
| `KnowledgeProvider` | Query a replaceable knowledge/index backend |
| `EventProvider` | Publish and read canonical platform events |
| `AuthorizationProvider` | Return normalized authorization decisions |
| `NodeProvider` | Discover participating compute nodes |
| `WorkerProvider` | Discover workers and dispatch canonical execution requests |

These interfaces are architectural seams, not product selections. Hermes, Forge, LiteLLM, MCP implementations, databases, policy engines, workflow engines and model runtimes may later implement one or more of them through adapters.

## Contract version

The initial provider contract version is `1.0`.

Every `ProviderDescriptor` declares the contract version it implements. Contract versioning is separate from:

- canonical domain schema versions;
- HTTP/API versions;
- adapter implementation versions;
- upstream project versions;
- model/provider versions.

Backward-compatible additions may remain within the same major contract version. Removing operations, changing parameter meaning, weakening guarantees or changing normalized return semantics requires a new major contract version.

## Provider identity

`provider_id` is a platform-facing identifier for a configured provider implementation. It must not be an opaque SDK object, database row or process handle.

Backend/runtime identifiers may be returned only in explicit external/backend reference fields such as `ExecutionHandle.backend_ref`. Canonical Task, Run, Event, Artifact and other domain identifiers remain platform-owned.

## Capability discovery

Every provider exposes a `ProviderDescriptor` and therefore a normalized capability list. `CapabilityProvider` additionally supplies a registry-style query boundary for aggregating/filtering capability records without inspecting concrete provider classes.

Capabilities contain:

- a neutral name;
- a broad `CapabilityKind`;
- a version;
- optional structured attributes.

Capability discovery is descriptive. It does not grant authorization and does not replace policy checks.

Later node/worker/tool/model matching logic must consume these neutral capabilities rather than inspect concrete provider classes.

## Operation context

Cross-provider calls use `OperationContext` to propagate logical metadata without binding providers to an observability or identity product.

The initial context reserves:

- `correlation_id`;
- optional `causation_id`;
- optional owner type/id;
- optional project ID.

Trace/span identifiers remain an observability concern and may be added at an API/telemetry boundary without replacing canonical correlation semantics.

## Error semantics

Provider-specific exceptions must not escape into unrelated platform modules.

Adapters translate failures into `ContractError` with a stable `ErrorCode`:

- `invalid_request`
- `unsupported_capability`
- `not_found`
- `conflict`
- `unavailable`
- `timeout`
- `unauthorized`
- `forbidden`
- `backend_error`

A contract error also records whether the operation is considered retryable and may include a provider identifier plus structured diagnostic details.

The contract layer does not define retry policy. The caller/lifecycle layer decides whether, when and how to retry based on canonical semantics.

## Backend-state containment

Adapters must translate backend state before crossing a contract boundary.

Not allowed in platform-facing contracts:

- Hermes session/runtime objects;
- Forge ORM/database entities;
- Temporal handles;
- LiteLLM/OpenAI SDK response objects;
- MCP transport/session objects;
- database client objects;
- vendor-specific exception classes.

Allowed:

- normalized dataclasses from `ai_multi_agent_platform.contracts.types`;
- primitive/JSON-compatible metadata;
- explicit opaque backend reference strings where the contract provides such a field.

## Reference implementations

`ai_multi_agent_platform.testing.fakes` contains intentionally small in-memory implementations for every initial contract family.

Their purpose is to prove that:

1. core code can execute against contracts without external services;
2. the interface does not require a specific upstream framework;
3. adapter replacement can be tested independently;
4. failure semantics can be exercised deterministically.

The fake providers are test utilities, not production defaults.

## Dependency direction

The required dependency direction is:

```text
canonical domain + contract types
        ↑
platform core
        ↑
adapter implementations
        ↑
external libraries/services/frameworks
```

External adapters may depend on platform contracts. Platform contracts must never import adapter or vendor packages.

## Deferred work

Issue #5 defines the seams only. It does not implement:

- task/run persistence or lifecycle orchestration;
- Hermes integration;
- Forge integration;
- model gateway selection policy;
- MCP transport;
- real memory/file/knowledge backends;
- distributed scheduling;
- authorization policy rules.

Those belong to later numbered issues and must conform to these boundaries.
