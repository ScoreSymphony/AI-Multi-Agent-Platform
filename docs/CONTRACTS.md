# Core Provider Contracts

This document defines the replaceable provider boundaries of the AI Multi-Agent Platform.
The contracts are platform-owned. Upstream systems implement adapters behind them; they do
not define canonical platform state or contract semantics.

## Contract families

| Contract | Responsibility |
| --- | --- |
| `CapabilityProvider` | Aggregate/filter normalized capabilities from a registry/source |
| `Orchestrator` | Produce provider-neutral plans for canonical task intent |
| `LifecycleBackend` | Start, observe and cancel canonical Run attempts |
| `ModelProvider` | Execute one provider-neutral model request |
| `ModelRouter` | Select a model/provider from canonical requirements |
| `ToolProvider` | Invoke tools independently from native/MCP/HTTP transport |
| `MemoryProvider` | Store/retrieve memory through canonical namespaces/references |
| `FileProvider` | Store/read payload bytes without leaking object-store IDs |
| `KnowledgeProvider` | Index, search and retrieve knowledge through canonical references |
| `EventProvider` | Publish/read canonical platform events |
| `AuthorizationProvider` | Return backend-neutral authorization decisions |
| `NodeProvider` | Register and discover canonical compute nodes |
| `WorkerProvider` | Register/discover workers and dispatch canonical executions |

Hermes, Forge, LiteLLM, MCP implementations, databases, policy engines, workflow engines and
model runtimes may later implement one or more of these interfaces through adapters.

## Contract versioning

The initial provider contract version was `1.0`. The current provider contract version is
`2.0`.

Provider Contract `2.0` introduces immutable `ToolInvocation.arguments` semantics so a tool
call that is mapped to a canonical approval/audit identity cannot be silently changed through
an alias to the original argument object. Adapters must use `ToolInvocation.arguments_json()`
when they need a detached standard JSON-serializable `dict`/`list` representation for HTTP,
MCP or another transport. That export is intentionally mutable because it is a copy; changing
it does not change the governed invocation snapshot.

This is a major-version change rather than a silent `1.0` change because a `1.0` adapter could
legitimately mutate or normalize the supplied argument dictionary in place. Contract `2.0`
removes that guarantee. A provider descriptor therefore must not advertise `1.0` while relying
on the immutable argument semantics.

Every `ProviderDescriptor` declares the contract version it implements. Provider-contract
versioning is independent from:

- canonical domain schema versions;
- public HTTP/API versions;
- adapter implementation versions;
- upstream project versions;
- model/provider versions.

Backward-compatible additions may remain within the same major contract version. Removing an
operation, changing parameter meaning, weakening a guarantee, changing canonical identity
semantics or changing normalized result/error behavior requires a new major contract version.

## Canonical identity

Canonical IDs are owned by the platform and cross provider boundaries unchanged.

Examples include:

- Task IDs;
- Run IDs;
- Event IDs;
- Artifact/object references;
- model request IDs;
- tool invocation IDs;
- node and worker IDs.

Backend/runtime identifiers may appear only in explicit backend-reference fields or in
namespaced `AdapterMetadata`. An opaque SDK object, ORM entity, transport/session object or
vendor exception must never become a canonical identifier.

## Provider and capability discovery

Every provider exposes a normalized `ProviderDescriptor` containing:

- `provider_id`;
- `provider_type`;
- contract version;
- supported operations;
- normalized capabilities;
- normalized health status;
- provider limits;
- optional namespaced adapter metadata.

Capabilities can describe:

- capability name and kind;
- capability version;
- supported operations;
- modalities;
- features;
- limits;
- portable structured attributes;
- namespaced adapter-private diagnostic metadata.

`CapabilityProvider` supplies a registry-style query boundary. Capability discovery is
descriptive; it does not grant authorization and does not replace policy checks.

Health is normalized through `HealthStatus` (`unknown`, `healthy`, `degraded`, `unavailable`).
Core code must not inspect provider-private health probe objects.

## Operation context, observability and control

Cross-provider calls use `OperationContext` for portable execution metadata. The initial
context carries:

- `correlation_id`;
- optional `causation_id`;
- optional owner type/id;
- optional project ID;
- `OperationControl`.

`correlation_id` and `causation_id` must be preserved across adapter boundaries when a request
is translated. Trace/span identifiers remain an observability-layer concern and may be added
later without replacing canonical correlation semantics.

### Timeout semantics

`OperationControl.timeout_seconds` expresses the maximum provider-boundary duration requested
by the caller. A configured timeout must be translated into the concrete backend's timeout
mechanism where possible. If the operation exceeds that boundary, the adapter returns
`ContractError(ErrorCode.TIMEOUT, ...)` rather than a provider-specific timeout exception.

A provider that cannot honor a required timeout must fail canonically rather than silently
pretend the guarantee exists.

### Retry semantics

`OperationControl.retry_mode` communicates caller intent:

- `never`: do not retry automatically at this boundary;
- `safe`: retry only operations the implementation can prove are safe to repeat;
- `idempotent`: retry is allowed when the canonical idempotency guarantee can be preserved.

The contract layer does not decide backoff, retry count or scheduling policy. Those decisions
belong to the calling lifecycle/control-plane layer.

### Idempotency

`OperationControl.idempotency_key` is the portable idempotency token for operations where one
is applicable. Canonical IDs remain authoritative; the key supplements rather than replaces
them.

For `LifecycleBackend.start`, repeating the same canonical Run attempt with the same semantic
request must not intentionally create a second execution. Adapters may map the Run ID and/or
idempotency key into backend-specific deduplication mechanisms.

## Cancellation semantics

Cancellation is explicit where the interface supports it. `LifecycleBackend.cancel` is
idempotent:

1. the request identifies the same canonical Run ID on every retry;
2. repeated cancellation must not create a new Run;
3. if the Run is already terminal, the backend returns the current normalized terminal state;
4. provider-specific cancellation exceptions are translated to canonical errors;
5. a backend that cannot cancel must report `unsupported_capability` rather than fabricate a
   cancelled state.

A cancellation caused by the provider boundary itself is represented by `ErrorCode.CANCELLED`.
A successfully cancelled execution is represented by canonical `ExecutionStatus.CANCELLED`.
These concepts are deliberately distinct.

## Error semantics

Provider-specific exceptions must not escape into unrelated platform modules. Adapters
translate failures into `ContractError` with a stable `ErrorCode`.

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

A `ContractError` also records whether the operation is retryable and may carry the configured
provider ID, portable diagnostic details and namespaced adapter-private diagnostics.

Recommended mapping rules:

- temporary network/backend outage -> `unavailable` or `transient_failure`;
- provider deadline -> `timeout`;
- provider cancellation -> `cancelled`;
- quota/throughput limit -> `rate_limited`;
- memory/compute/storage capacity limit -> `resource_exhausted`;
- invalid caller configuration/input -> `invalid_request`;
- missing operation/modality -> `unsupported_capability`;
- authentication failure -> `unauthorized`;
- policy denial -> `forbidden`;
- non-retryable execution/provider failure -> `permanent_failure`;
- malformed provider response or contract breach -> `contract_violation`.

Raw third-party exceptions may be chained as the Python cause for diagnostics, but callers must
be able to reason entirely from canonical error fields.

## Adapter-specific metadata isolation

Backend-private metadata is allowed only through `AdapterMetadata` and must use an explicit
namespace.

Example conceptual shape:

```text
adapter_metadata:
  - namespace: forge
    values:
      execution_id: ...
  - namespace: litellm
    values:
      model_group: ...
```

Portable fields such as canonical IDs, health, capabilities and normalized limits must not be
hidden inside adapter metadata merely because doing so is convenient for one backend.

Namespaces must be non-empty and contain no spaces. Conformance checks additionally require
namespaces in one metadata collection to be unique.

## Backend-state containment

Not allowed in platform-facing contracts:

- Hermes session/runtime classes;
- Forge ORM/database entities;
- Temporal handles;
- LiteLLM/OpenAI SDK response classes;
- MCP session/transport objects;
- database-client objects;
- vendor-specific exception classes.

Allowed:

- platform-owned contract/domain dataclasses;
- primitive/JSON-compatible portable metadata;
- explicit opaque backend reference strings where a contract provides such a field;
- namespaced `AdapterMetadata` for backend-private diagnostics.

## Knowledge, node and worker boundaries

`KnowledgeProvider` defines index/search/retrieve operations because a backend-neutral knowledge
contract must cover the complete minimum lifecycle rather than expose only search.

`NodeProvider` and `WorkerProvider` define registration plus discovery. Registration is a
canonical platform operation; adapters may map it to a database row, heartbeat, service
registry or another concrete mechanism without leaking that mechanism into callers.

## Reference/fake implementations

`ai_multi_agent_platform.testing.fakes` contains deterministic in-memory implementations for
the initial contract families.

The fakes support the P0 development requirements:

- successful operations;
- deterministic outputs;
- configurable canonical failures;
- timeout simulation through configured `ErrorCode.TIMEOUT` failures;
- execution cancellation and terminal-state simulation;
- call recording for assertions;
- idempotent lifecycle start/cancel behavior;
- knowledge index/query/get;
- node/worker registration and discovery.

They are test utilities, not production defaults.

## Reusable conformance suite

`ai_multi_agent_platform.testing.conformance` contains pytest-independent checks that accept a
configured provider instance. Production adapters should run the same checks used by the fake
providers.

The initial checks validate, where applicable:

- provider identity and contract version;
- capability/health normalization;
- namespaced adapter metadata;
- canonical request/invocation/Run IDs;
- idempotent lifecycle start/cancel behavior;
- knowledge source references;
- node/worker registration and discovery;
- backend-private exception containment.

Adapter-specific test modules may add stronger tests, but they must not replace the common
conformance suite.

## Dependency direction

The required dependency direction is:

```text
canonical domain + contract types
        ^
platform core
        ^
adapter implementations
        ^
external libraries/services/frameworks
```

External adapters may depend on platform contracts. Platform core/contracts must not import
concrete adapters or optional vendor SDKs. CI scans the core source graph and imports core
modules in an environment that does not install Hermes, Forge, LiteLLM, MCP or similar
optional adapters.

## Required validation scenarios

Issue #5 is guarded by explicit tests for these scenarios:

1. run one canonical Task flow using only fake orchestrator, model, tool and lifecycle
   providers;
2. replace the model implementation with a second independent test adapter without changing
   canonical request/domain objects;
3. force a backend-private exception in a test adapter and prove it becomes `ContractError`;
4. import the core package graph without optional adapter dependencies installed.

## Deferred work

Issue #5 defines the seams and their enforceable behavior. It does not implement:

- production task/run persistence or lifecycle orchestration;
- Hermes integration;
- Forge integration;
- production model-routing policy;
- MCP transport;
- production memory/file/knowledge backends;
- distributed scheduling;
- final authorization policy rules.

Those belong to later numbered issues and must conform to these boundaries.
