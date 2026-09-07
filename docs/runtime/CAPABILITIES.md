# Capability Registry and Tool Invocation

Issue: #12

## Architectural rule

The platform owns capability identity, schemas, compatibility metadata, policy hooks, trace metadata and invocation semantics. A concrete tool backend implements `CapabilityToolProvider`. MCP is one optional adapter and is never the canonical tool model.

Canonical invocation flow:

```text
CapabilityInvocation
        |
        v
CapabilityRegistry.resolve()
        |
        +-- exact version OR canonical compatibility request
        +-- feature / permission / worker placement checks
        |
        v
CapabilityInvoker
        |
        +-- JSON Schema input validation
        +-- optional canonical ToolInvocation binding for every resolved call
        +-- policy hook
        +-- approval hook bound to tool_invocation_* identity when required
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

## Inventory versus policy-aware discovery

`CapabilityRegistry.list_capabilities()` is the synchronous static inventory view. It filters availability, health, declared permissions and worker placement but deliberately does **not** claim that a capability is authorized for a caller. It remains useful for administration, diagnostics and composition code that needs a backend-neutral inventory.

Caller-facing usable discovery uses `CapabilityRegistry.discover_capabilities()` with a canonical `CapabilityDiscoveryRequest`. The request carries the existing `OperationContext` plus granted permissions and available worker capabilities. A replaceable `CapabilityDiscoveryPolicyHook` receives that caller/scope context and each otherwise eligible `CapabilitySpec`:

- `DENY` removes the capability from caller-visible discovery;
- `ALLOW` keeps it visible;
- `REQUIRE_APPROVAL` keeps it visible because it remains usable through the canonical approval path.

The hook is an integration seam, not the final authorization engine. It works without a concrete implementation of issue #15 and can later delegate to that platform policy backend. Invocation remains the enforcement point and evaluates its invocation policy hook again immediately before governance/provider execution; a discovery result is never treated as a cached authorization grant.

## Version and feature compatibility

Exact backend-neutral version selection remains available through `CapabilityInvocation.version` / `CapabilityRegistry.resolve(..., version=...)`. Exact matching can use any non-blank provider version identifier because no compatibility is inferred.

When the caller needs compatible selection it uses `CapabilityCompatibilityRequest` instead. The request can declare:

- a minimum version and whether that bound is inclusive;
- a maximum version and whether that bound is inclusive;
- required canonical feature names.

`CapabilitySpec.features` publishes the provider-neutral feature set of a capability version. Compatible resolution first filters by required features, then applies the version range, then chooses the highest matching version deterministically.

Compatibility ordering intentionally supports only one-to-three-part dotted numeric versions such as `1`, `1.4` or `2.0.3`. This is the canonical baseline and avoids silently inventing ordering semantics for labels such as `stable`, provider build identifiers or other opaque strings. Such versions remain fully usable by exact selection. If compatibility would require ordering an opaque label, or two distinct labels normalize to the same numeric compatibility version (for example `1.0` and `1.0.0`), resolution fails with a canonical conflict and requires exact selection.

If no version satisfies the requested bounds/features, the registry returns a canonical `UNSUPPORTED_CAPABILITY` error with available versions and the compatibility request details.

`version` and `compatibility` are mutually exclusive on a canonical invocation.

## Credential-requiring capabilities

Credential need is first-class capability metadata through `CredentialRequirement`:

- `none` — the capability does not declare a credential requirement;
- `required` — use requires credential material supplied through the appropriate security/configuration path.

This classification is deliberately separate from safety sensitivity, side-effect classification, permissions and approvals. Discovery and invocation policy hooks receive the `CapabilitySpec`, so they can apply policy to credential-requiring capabilities without learning where credentials are stored or how they are fetched.

The capability contract contains **no secret value, secret reference, secret provider, vault type or backend credential object**. Secret storage/retrieval remains outside #12 and belongs to the dedicated configuration/secrets and authorization boundaries.

## Capability identity versus canonical invocation identity

`CapabilityInvocation.invocation_id` is the provider-neutral request handle used to correlate one provider call. It is not itself the canonical domain ToolInvocation identity.

`CapabilityInvoker` now exposes `CanonicalInvocationBindingHook` as the ordinary identity seam. When configured, the hook runs for every resolved invocation before policy, approval and provider execution. Lifecycle records and successful results can therefore retain one canonical `tool_invocation_*` subject for allowed, denied, failed, timed-out and successful calls instead of creating canonical identity only when approval happens to be required.

The reference Agent composition uses `bind_canonical_capability_invocation(...)`. It derives a stable canonical `tool_*` identity from canonical capability ID plus exact version, and a stable `tool_invocation_*` identity from the canonical Run, the platform-owned invocation key, canonical Tool identity and immutable argument digest. Provider/model invocation handles and `provider_tool_ref` remain external references only and do not determine canonical identity. The binder requires owner context from the canonical runtime and fails closed rather than inventing ownership.

For Agent model tool calls, the platform invocation key is derived from the canonical Run plus the tool-call ordinal (`<run_id>:capability:<ordinal>`). A model/provider `call_id` is retained only as evidence in the normalized capability result and never becomes `AgentRun.tool_invocation_refs`.

The shared contract/domain mapping records the immutable argument digest introduced by ADR 0001. `validate_tool_invocation_binding(...)` runs after canonical mapping and again immediately before provider execution, proving that the resolved provider tool, provider invocation handle, context and argument snapshot still match the canonical ToolInvocation.

`GovernanceBindingHook` remains supported as a backwards-compatible approval-only fallback. If approval is required and no ordinary canonical binding exists, the governance hook may create the same canonical domain `ToolInvocation`. If neither canonical nor governance binding is available, approval-required execution fails with a contract violation instead of bypassing governance.

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
- credential requirement;
- required permissions;
- required approvals;
- required worker capabilities;
- version-compatible feature metadata.

The registry rejects missing permissions before provider execution. Policy-aware discovery can hide denied capabilities before callers treat them as usable. The invocation policy hook can allow, deny or require approval. Approval decisions receive the canonical governed `tool_invocation_*` object. Invocation records retain `approved` or `required` where an approval decision is applicable.

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
