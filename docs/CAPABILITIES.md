# Capability Registry and Tool Invocation

Issue: #12

## Architectural rule

The platform owns capability identity, schemas, policy hooks, trace metadata and
invocation semantics. A concrete tool backend implements `CapabilityToolProvider`.
MCP is one adapter and is never the canonical tool model.

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
        +-- approval hook
        +-- timeout / cancellation
        |
        v
ToolProvider.invoke(ToolInvocation)
        |
        v
CapabilityInvocationResult + InvocationRecord
```

Provider-private tool names only appear in `CapabilityRegistration.provider_tool_ref`
and adapter metadata. Agents request `capability_id`, not MCP/native/backend names.

## Reference native capability

`NativeEchoProvider` publishes:

- capability ID: `tool.echo`
- version: `1.0`
- side effects: none
- deterministic request: `{"message": "..."}`
- deterministic result: `{"message": "..."}`

It exists so the complete canonical path can be tested with MCP disabled.

## MCP adapter

`MCPToolProvider` maps discovered MCP tools to `CapabilitySpec` and translates
canonical provider invocations into `MCPClient.call_tool(...)`.

`MCPServerConfig` supports either:

- an endpoint definition; or
- a process command definition.

A concrete MCP transport/SDK client implements the intentionally small `MCPClient`
protocol. This keeps the platform core importable and testable without installing
an MCP SDK. Transport/session identifiers are not exposed in canonical requests or
results.

For known tools, `capability_id_overrides` should be used to assign stable semantic
capability IDs. When no override exists, discovery falls back to a backend-neutral
`tool.<normalized-name>` ID. Collisions are surfaced by the registry instead of
silently picking a provider.

## Policy and approval hooks

Issue #12 defines hooks only. Issue #15 remains responsible for the final
authorization/approval backend.

The capability contract distinguishes:

- safety classification;
- side-effect classification;
- required permissions;
- required approvals;
- required worker capabilities.

The invocation path rejects missing permissions before provider execution. A
policy hook can allow, deny or require approval. Required approval labels can be
satisfied by invocation grants or a future approval hook.

## Traceability

Every `CapabilityInvocation` carries an `InvocationTrace` with:

- correlation ID;
- task ID;
- run ID;
- agent ID;
- optional project ID;
- optional causation ID.

Observers receive lifecycle `InvocationRecord` values without raw arguments or
outputs, reducing accidental sensitive-data exposure at the default audit seam.

## Current first-slice boundary

This implementation establishes the canonical contracts, registry, invocation
pipeline, deterministic native provider and MCP adapter seam. A concrete network
or stdio MCP SDK transport can be plugged into `MCPClient` without changing the
canonical API. Kernel/event-store persistence of invocation records belongs to the
later observability/integration work and can implement `InvocationObserver`.
