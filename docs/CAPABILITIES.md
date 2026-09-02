# Capability Registry and Tool Invocation

Issue: #12

## Architectural rule

The platform owns capability identity, schemas, policy hooks, trace metadata and invocation semantics. A concrete tool backend implements `CapabilityToolProvider`. MCP is one optional adapter and is never the canonical tool model.

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
        +-- result / artifact / evidence references
        |
        v
CapabilityInvocationResult + InvocationRecord
        |
        v
optional EventRepositoryInvocationObserver
```

Provider-private tool names only appear in `CapabilityRegistration.provider_tool_ref` and namespaced adapter metadata. Agents request `capability_id`, not MCP/native/backend names.

## Capability identity versus governed invocation identity

`CapabilityInvocation.invocation_id` is the provider-neutral request handle used to correlate one provider call. It is not the canonical governance identity defined by the domain model.

When policy or capability metadata requires approval, `GovernanceBindingHook` must map the resolved provider invocation to the existing canonical domain `ToolInvocation` (`tool_invocation_*`). The binding uses the existing contract/domain mapping rules, including the immutable argument digest introduced by ADR 0001.

The invocation pipeline calls `validate_tool_invocation_binding(...)` after the canonical mapping and again immediately before provider execution. An approval therefore applies to the exact resolved provider tool, context and argument snapshot rather than to a reusable capability or mutable request handle.

If approval is required but no canonical governance binding is configured, execution fails with a canonical contract violation rather than bypassing governance. Lifecycle audit records preserve the approval decision when applicable.

## Reference native capability

`NativeEchoProvider` publishes:

- capability ID: `tool.echo`
- version: `1.0`
- side effects: none
- deterministic request: `{"message": "..."}`
- deterministic result: `{"message": "..."}`

It exists so the complete canonical path can be tested with MCP disabled.

## MCP adapter

The MCP integration is split into two layers:

- `ai_multi_agent_platform.adapters.mcp` contains the platform-owned adapter projection and the small `MCPClient` protocol;
- `ai_multi_agent_platform.adapters.mcp_sdk` contains the concrete implementation using the official MCP Python SDK.

The core capability package never imports the MCP SDK. Baseline/native use therefore remains functional when the optional MCP dependency is absent.

Install the real transport integration with:

```bash
python -m pip install -e '.[mcp]'
```

The repository pins the optional SDK to `mcp==2.1.1`. CI also installs that version through the development extra so the real transport integration is continuously tested.

### Streamable HTTP

```python
from ai_multi_agent_platform.adapters.mcp import MCPServerConfig
from ai_multi_agent_platform.adapters.mcp_sdk import build_mcp_provider

config = MCPServerConfig(
    server_id="remote-tools",
    endpoint="https://tools.example.invalid/mcp",
    read_timeout_seconds=30,
)
provider = build_mcp_provider(config)
```

### stdio subprocess

```python
from ai_multi_agent_platform.adapters.mcp import MCPServerConfig
from ai_multi_agent_platform.adapters.mcp_sdk import build_mcp_provider

config = MCPServerConfig(
    server_id="local-tools",
    command=("python", "server.py"),
    environment={"EXAMPLE_MODE": "1"},
    cwd="/srv/tools",
    read_timeout_seconds=30,
)
provider = build_mcp_provider(config)
```

Exactly one of `endpoint` or `command` must be supplied. Environment values are transport inputs and are deliberately not copied into adapter metadata.

`MCPToolProvider` maps discovered MCP tools to `CapabilitySpec`, including input and output schemas when supplied by the server. Canonical provider invocations are translated into MCP calls and SDK/Pydantic result objects are normalized to JSON values before crossing back into canonical APIs. SDK, session and transport objects therefore do not leak into platform contracts.

For known tools, `capability_id_overrides` should be used to assign stable semantic capability IDs. When no override exists, discovery falls back to `tool.<normalized-name>`. Collisions are surfaced by the registry instead of silently picking a provider.

The repository includes a real stdio integration test that launches an MCP server subprocess with the official SDK and invokes its tool through `CapabilityRegistry` and `CapabilityInvoker`; the fake client remains for deterministic error/contract tests.

## Policy and approval hooks

Issue #12 defines integration hooks only. Issue #15 remains responsible for the final authorization/approval backend.

The capability contract distinguishes:

- safety classification;
- side-effect classification;
- required permissions;
- required approvals;
- required worker capabilities.

The registry rejects missing permissions before provider execution. A policy hook can allow, deny or require approval. Approval decisions receive the canonical governed `tool_invocation_*` object. Invocation records retain `approved` or `required` where an approval decision is applicable.

## Schema validation

Declared capability inputs are validated before the provider is invoked. Declared output schemas are validated before the provider result is accepted as a successful canonical result.

The implementation uses the pinned `jsonschema==4.26.0` runtime dependency behind the platform-owned invocation boundary. No `jsonschema` library object appears in canonical public contracts. Provenance, licensing, dependency review and replacement strategy are recorded in `docs/upstream/JSONSCHEMA_ADOPTION.md` and `docs/UPSTREAMS.md`.

## Result, artifact and evidence references

`ToolResult` and `CapabilityInvocationResult` carry optional:

- `result_ref`;
- `artifact_refs`;
- `evidence_refs`.

The invocation pipeline propagates these references without requiring a provider to inline large or unstructured data. Providers that only have a structured JSON value can continue to use `output` alone.

## Durable traceability

Every `CapabilityInvocation` carries an `InvocationTrace` with canonical:

- correlation ID;
- task ID;
- run ID;
- agent ID;
- optional project ID;
- optional causation ID.

Resolved provider placement can additionally contribute canonical node and worker IDs to `InvocationRecord`.

`EventRepositoryInvocationObserver` persists lifecycle records through the existing canonical `EventRepository`. It uses a dedicated stream per capability invocation, so capability audit events do not change task/run reducer semantics. The SQLite kernel repository therefore provides a durable restart-safe baseline without introducing another storage system.

Example:

```python
from ai_multi_agent_platform.capabilities import EventRepositoryInvocationObserver
from ai_multi_agent_platform.kernel.sqlite_repository import SqliteKernelRepository

repository = SqliteKernelRepository("platform.db")
observer = EventRepositoryInvocationObserver(repository)
```

The default audit event deliberately excludes raw tool arguments and outputs. It stores IDs, status, provider metadata, approval decision, placement and errors. This is the default redaction boundary for #12; richer sensitive-data policy remains the responsibility of the authorization/observability layers.

## Optionality and replacement

Removing the `mcp` extra removes only `adapters.mcp_sdk`. Canonical capability contracts, registry logic, native tools and core architecture tests do not import it. A different MCP implementation can satisfy the small `MCPClient` protocol without changing agent/task contracts.

The official SDK adoption and exit strategy are documented in `docs/upstream/MCP_PYTHON_SDK_ADOPTION.md` and `docs/UPSTREAMS.md`.
