# Core Provider Contracts

This document defines the replaceable provider boundaries of the AI Multi-Agent Platform. The contracts are platform-owned. Upstream systems implement adapters behind them; they do not define canonical platform state, identity or lifecycle semantics.

## Contract families

| Contract | Responsibility |
| --- | --- |
| `CapabilityProvider` | Aggregate/filter normalized provider capabilities |
| `Orchestrator` | Produce provider-neutral planning/coordination proposals |
| `LifecycleBackend` | Start, observe and cancel canonical Run attempts |
| `ModelProvider` | Execute one provider-neutral model request |
| `ModelRouter` | Select a provider/model through a typed platform result |
| `ToolProvider` | Invoke canonical Tools independently from native/MCP/HTTP transport |
| `MemoryProvider` | Store/retrieve memory through portable namespaces/references |
| `FileProvider` | Store/read payload bytes without leaking object-store IDs |
| `KnowledgeProvider` | Index, search and retrieve knowledge through portable references |
| `EventProvider` | Publish/read/subscribe to canonical domain Events |
| `AuthorizationProvider` | Return backend-neutral authorization decisions |
| `NodeProvider` | Register and discover nodes using canonical Node IDs |
| `WorkerProvider` | Register/discover workers and dispatch canonical Runs |

Hermes, Forge, LiteLLM, MCP implementations, databases, policy engines, workflow engines and model runtimes may later implement one or more of these interfaces through adapters.

## Canonical domain alignment

The provider layer is not a second domain model. Contract types may package inputs/results for an adapter call, but they must reuse canonical identity and lifecycle semantics from `ai_multi_agent_platform.domain`.

The following invariants are enforced in code and tests:

- `ExecutionStatus` is the canonical domain `RunStatus`, including `queued`, `starting`, `running` and all terminal states. There is no second execution-status enum in the contract layer.
- `PlatformEvent` is an import alias of the canonical domain `Event`; `EventProvider` therefore cannot introduce a parallel event record with different identity, timestamp or subject semantics.
- `PlanRequest.task_id` must be a canonical `task_<uuid>` ID.
- `ExecutionRequest.run_id` must be a canonical `run_<uuid>` ID and its subject must be a canonical Task or Step ID matching `subject_type`.
- execution handles/snapshots preserve the same canonical Run ID.
- `ToolInvocation.tool_ref` is a canonical Tool ID. Native/MCP/HTTP/provider tool identifiers are adapter-private mappings, not canonical IDs.
- node and worker descriptors require canonical `node_<uuid>` / `worker_<uuid>` identities.
- optional `project_id` values in operation context use canonical Project IDs.

Backend/runtime identifiers may appear only in explicit backend-reference fields, external references owned by the canonical domain, or namespaced adapter diagnostics. They never replace canonical primary or relationship IDs.

### Orchestrator ownership boundary

An orchestrator proposes planning content; it does not allocate canonical Plan or Step identities.

`PlanResponse` therefore contains a summary and proposal-local `PlanStepProposal` records. Proposal keys are local to that response and are **not** canonical IDs. The platform kernel/application layer converts accepted planning content into canonical `Plan` and `Step` entities and assigns their IDs.

This prevents a Hermes workflow ID, another orchestrator's step ID or a fake-provider identifier from becoming the platform's Plan/Step identity.

### Discovery views versus canonical entities

Provider `Capability`, `NodeDescriptor` and `WorkerDescriptor` types are normalized contract/discovery views, not competing persisted domain entities. Their identity-bearing fields must use canonical IDs where the domain defines one. Persisted ownership, provenance, lifecycle and relationship state remain in the canonical domain model.

## Contract versioning

The initial provider contract version is `1.0`.

Every `ProviderDescriptor` declares the contract version it implements. Provider-contract versioning is independent from:

- canonical domain schema versions;
- public HTTP/API versions;
- adapter implementation versions;
- upstream project versions;
- model/provider versions.

Backward-compatible additions may remain within the same major contract version. Removing an operation, changing parameter meaning, weakening a guarantee, changing canonical identity semantics or changing normalized result/error behavior requires a new major contract version.

## Provider and capability discovery

Every provider exposes a normalized `ProviderDescriptor` containing:

- provider/adapter identifier;
- provider type;
- contract version;
- supported operations;
- normalized capabilities;
- normalized health/availability;
- limits/resources;
- optional namespaced adapter metadata.

Capabilities can describe name/kind/version, supported operations, modalities, features, limits, portable structured attributes and namespaced adapter-private diagnostics.

`CapabilityProvider` supplies a registry-style query boundary. Discovery is descriptive; it does not grant authorization and does not replace policy checks.

Health uses `HealthStatus` (`unknown`, `healthy`, `degraded`, `unavailable`). Core code must not inspect provider-private probe objects.

## Operation context, observability and control

Cross-provider calls use `OperationContext` for portable execution metadata. It carries:

- `correlation_id`;
- optional `causation_id`;
- optional owner type/id;
- optional canonical Project ID;
- `OperationControl`.

Correlation and causation values must survive adapter translation. Provider-specific trace/session objects do not cross the boundary.

### Timeout semantics

`OperationControl.timeout_seconds` expresses the maximum provider-boundary duration requested by the caller. A configured timeout must be translated into the concrete backend's timeout mechanism where possible. If the operation exceeds that boundary, the adapter raises `ContractError(ErrorCode.TIMEOUT, ...)` rather than a provider-specific timeout exception.

A provider that cannot honor a required timeout must fail canonically instead of silently weakening the guarantee.

### Retry semantics

`OperationControl.retry_mode` communicates caller intent:

- `never`: do not retry automatically at this boundary;
- `safe`: retry only operations the implementation can prove are safe to repeat;
- `idempotent`: retry is allowed when canonical idempotency can be preserved.

Backoff, retry count and scheduling policy belong to the calling lifecycle/control-plane layer.

### Idempotency

`OperationControl.idempotency_key` is the portable idempotency token where applicable. Canonical IDs remain authoritative; the key supplements rather than replaces them.

For `LifecycleBackend.start`, repeating the same canonical Run attempt with the same semantic request must not intentionally create a second execution. Adapters may map the Run ID and/or idempotency key into backend-specific deduplication mechanisms.

## Cancellation semantics

`LifecycleBackend.cancel` is idempotent:

1. retries identify the same canonical Run ID;
2. repeated cancellation cannot create a new Run;
3. if the Run is already terminal, the backend returns the current normalized terminal state;
4. provider-specific cancellation exceptions are translated to canonical errors;
5. a backend that cannot cancel reports `unsupported_capability` instead of fabricating cancellation.

Boundary-operation cancellation is `ErrorCode.CANCELLED`; a successfully cancelled Run is canonical `RunStatus.CANCELLED`. These are distinct concepts.

## Error semantics

Provider-specific exceptions must not escape into unrelated platform modules. Adapters translate failures into `ContractError` with a stable `ErrorCode`.

Initial categories are:

- `invalid_request`;
- `unsupported_capability`;
- `not_found`;
- `conflict`;
- `unavailable`;
- `timeout`;
- `cancelled`;
- `rate_limited`;
- `resource_exhausted`;
- `unauthorized`;
- `forbidden`;
- `transient_failure`;
- `permanent_failure`;
- `contract_violation`;
- `backend_error`.

A `ContractError` records whether the operation is retryable and may carry provider ID, portable diagnostic details and namespaced adapter-private diagnostics.

Recommended mappings:

- temporary network/backend outage -> `unavailable` or `transient_failure`;
- provider deadline -> `timeout`;
- provider cancellation -> `cancelled`;
- quota/throughput limit -> `rate_limited`;
- memory/compute/storage capacity -> `resource_exhausted`;
- invalid caller configuration/input -> `invalid_request`;
- missing operation/modality -> `unsupported_capability`;
- authentication failure -> `unauthorized`;
- policy denial -> `forbidden`;
- non-retryable execution/provider failure -> `permanent_failure`;
- malformed provider response or contract breach -> `contract_violation`.

Raw third-party exceptions may be chained as the Python cause for diagnostics, but callers reason entirely from canonical error fields.

## Adapter-specific metadata isolation

Backend-private metadata is allowed only through `AdapterMetadata` and must use an explicit namespace.

```text
adapter_metadata:
  - namespace: forge
    values:
      execution_id: ...
  - namespace: litellm
    values:
      model_group: ...
```

Portable fields such as canonical IDs, health, capabilities and normalized limits must not be hidden in adapter metadata. Namespaces must be non-empty and contain no spaces; conformance checks also require uniqueness within one metadata collection.

Canonical domain Events themselves do not gain an adapter-specific metadata field merely for an event transport. Event adapters must preserve the domain Event unchanged; transport diagnostics remain in adapter/error/observability data.

## Backend-state containment

Not allowed in platform-facing contracts:

- Hermes session/runtime classes;
- Forge ORM/database entities;
- Temporal workflow handles;
- LiteLLM/OpenAI SDK response classes;
- MCP session/transport objects;
- database-client objects;
- vendor-specific exception classes.

Allowed:

- canonical domain types;
- platform-owned contract request/result types;
- JSON-compatible portable metadata;
- explicit opaque backend reference strings in fields designed for that purpose;
- namespaced `AdapterMetadata` for backend-private diagnostics.

## Reference/fake implementations

`ai_multi_agent_platform.testing.fakes` contains deterministic in-memory implementations for every initial interface family. They support:

- successful deterministic outputs;
- configurable canonical failures, including timeout simulation;
- execution cancellation and terminal-state simulation;
- call recording;
- idempotent lifecycle start/cancel behavior;
- knowledge index/query/get;
- event cursor/subscription behavior using canonical Events;
- node/worker registration and discovery using canonical IDs.

They are test utilities, not production defaults.

## Reusable conformance suite

`ai_multi_agent_platform.testing.conformance` is pytest-independent and accepts configured provider instances. Production adapters must be able to run the same checks used by reference providers.

The suite validates, where applicable:

- provider identity, contract version and health/capabilities;
- namespaced adapter metadata;
- canonical Task/Run/Tool/Node/Worker identities at boundaries;
- canonical RunStatus use;
- orchestrator non-ownership of Plan/Step identity;
- typed model-router results;
- idempotent lifecycle start/cancel behavior;
- event identity/cursor behavior using the canonical domain Event;
- knowledge source references;
- node/worker registration and dispatch;
- backend-private exception containment.

Adapter-specific tests may add stronger requirements but must not replace the common suite.

## Dependency direction

The required dependency direction is:

```text
canonical domain
      ^
provider contract types/interfaces
      ^
platform core/application services
      ^
adapter implementations
      ^
external libraries/services/frameworks
```

External adapters may depend on platform contracts. Canonical domain/core/contracts must not import concrete adapters or optional vendor SDKs. CI scans the core source graph and imports core modules in an environment without Hermes, Forge, LiteLLM, MCP, Temporal or OpenAI adapter dependencies.

## Required validation scenarios

Issue #5 is guarded by explicit tests for all requested scenarios:

1. a real canonical `Task`, `Run` and `Tool` execute through fake orchestrator, model, tool and lifecycle providers only;
2. the model implementation is replaced by a second independent adapter without modifying the canonical Task or Agent objects;
3. a backend-private exception is forced and verified to become canonical `ContractError`;
4. the complete core import graph loads without optional adapter dependencies.

Additional regression tests prevent duplicate domain/contract lifecycle types and reject non-canonical entity IDs at provider boundaries.

## Deferred work

Issue #5 defines architectural seams and enforceable adapter behavior. It does not implement:

- production task/run persistence or lifecycle orchestration;
- Hermes or Forge integrations;
- production model-routing policy;
- MCP transport;
- production memory/file/knowledge backends;
- distributed scheduling;
- final authorization policy rules.

Those belong to later numbered issues and must conform to these boundaries.
