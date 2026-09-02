# Capability Registry and Tool Invocation

Issue: #12

## Architectural rule

The platform owns capability identity, schemas, policy hooks, trace metadata and invocation semantics. A concrete tool backend implements `CapabilityToolProvider`. MCP is one adapter and is never the canonical tool model.

Canonical flow:

```text
CapabilityInvocation
        |
        v
CapabilityRegistry.resolve()
        |
        +-- version / permission / worker placement checks
        |
        v
CapabilityInvoker
        |
        +-- JSON Schema input validation
        +-- policy hook
        +-- canonical governance binding when approval is required
        +-- approval hook bound to tool_invocation_* identity
        +-- pre-execution binding verification
        +-- timeout / cancellation
        |
        v
ToolProvider.invoke(ToolInvocation)
        |
        v
CapabilityInvocationResult + InvocationRecord
```

Provider-private tool names only appear in `CapabilityRegistration.provider_tool_ref` and adapter metadata. Agents request `capability_id`, not MCP/native/backend names.

## Capability identity versus governed invocation identity

`CapabilityInvocation.invocation_id` is the provider-neutral request handle used to correlate one provider call. It is not the canonical governance identity defined by the domain model.

When policy or capability metadata requires approval, `GovernanceBindingHook` must map the resolved provider invocation to the existing canonical domain `ToolInvocation` (`tool_invocation_*`). The binding uses the existing contract/domain mapping rules, including the immutable argument digest introduced by ADR 0001.

The invocation pipeline calls `validate_tool_invocation_binding(...)` after the canonical mapping and again immediately before provider execution. An approval therefore applies to the exact resolved provider tool, context and argument snapshot rather than to a reusable capability or mutable request handle.

If approval is required but no canonical governance binding is configured, execution fails with a canonical contract violation rather than bypassing governance.

## Reference native capability

`NativeEchoProvider` publishes:

- capability ID: `tool.echo`
- version: `1.0`
- side effects: none
- deterministic request: `{"message": "..."}`
- deterministic result: `{"message": "..."}`

It exists so the complete canonical path can be tested with MCP disabled.

## MCP adapter

`MCPToolProvider` maps discovered MCP tools to `CapabilitySpec` and translates canonical provider invocations into `MCPClient.call_tool(...)`.

`MCPServerConfig` supports either:

- an endpoint definition; or
- a process command definition.

A concrete MCP transport/SDK client implements the intentionally small `MCPClient` protocol. This keeps the platform core importable and testable without installing an MCP SDK. Transport/session identifiers are not exposed in canonical requests or results.

For known tools, `capability_id_overrides` should be used to assign stable semantic capability IDs. When no override exists, discovery falls back to a backend-neutral `tool.<normalized-name>` ID. Collisions are surfaced by the registry instead of silently picking a provider.

## Policy and approval hooks

Issue #12 defines integration hooks only. Issue #15 remains responsible for the final authorization/approval backend.

The capability contract distinguishes:

- safety classification;
- side-effect classification;
- required permissions;
- required approvals;
- required worker capabilities.

The registry rejects missing permissions before provider execution. A policy hook can allow, deny or require approval. Approval decisions receive the canonical governed `tool_invocation_*` object so later authorization work can attach canonical `Approval` and `Event` records without adopting provider-private IDs.

## Schema validation

Declared capability inputs are validated before the provider is invoked. Declared output schemas are validated before the provider result is accepted as a successful canonical result.

The implementation uses the pinned `jsonschema==4.26.0` runtime dependency behind the platform-owned invocation boundary. No `jsonschema` library object appears in canonical public contracts. Provenance, licensing, dependency review and replacement strategy are recorded in `docs/upstream/JSONSCHEMA_ADOPTION.md` and `docs/UPSTREAMS.md`.

## Traceability

Every `CapabilityInvocation` carries an `InvocationTrace` with canonical:

- correlation ID;
- task ID;
- run ID;
- agent ID;
- optional project ID;
- optional causation ID.

Task, Run, Agent and Project identifiers are validated as canonical domain IDs. Trace and `OperationContext` correlation, causation and project values must agree.

Observers receive lifecycle `InvocationRecord` values without raw arguments or outputs, reducing accidental sensitive-data exposure at the default audit seam. Governed invocations also carry the canonical `tool_invocation_*` ID in result/audit records.

## Current first-slice boundary

This implementation establishes the canonical contracts, registry, invocation pipeline, governance/approval seam, deterministic native provider and MCP adapter seam. A concrete network or stdio MCP SDK transport can be plugged into `MCPClient` without changing the canonical API.

Kernel/event-store persistence of invocation records belongs to the later observability/integration work and can implement `InvocationObserver`. The final authorization backend belongs to #15; worker placement becomes richer with #14.
