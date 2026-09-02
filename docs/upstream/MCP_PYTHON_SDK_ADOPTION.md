# MCP Python SDK Optional Adapter Adoption Review

- **Project name:** Model Context Protocol Python SDK
- **Canonical upstream repository:** https://github.com/modelcontextprotocol/python-sdk
- **Adopted version:** 2.1.1
- **Integration category:** optional adapter/library dependency
- **Platform boundary:** `ai_multi_agent_platform.adapters.mcp_sdk`
- **Reviewer:** ScoreSymphony
- **Review date:** 2026-09-02
- **Related issue:** #12

## Decision

**Approved** as an optional implementation of the platform-owned `MCPClient` boundary.

The SDK is not a canonical platform dependency. Capability identity, schemas, policy, governance, traces, errors, evidence references and provider routing remain platform-owned. Installing the base package does not install MCP; users that need the official transport install the `mcp` extra.

## Version and provenance

- [x] Canonical upstream verified as `modelcontextprotocol/python-sdk`.
- [x] Version `2.1.1` is the latest stable release at review time (released 2026-08-25).
- [x] Production dependency is pinned exactly as `mcp==2.1.1` in the optional `mcp` extra.
- [x] Development/CI installs the same exact version so integration drift is visible.
- [x] Upstream package requires Python >=3.10; the platform requires Python >=3.12.
- [x] Upstream classifies the package as production/stable.

## License review

The SDK is MIT licensed. No SDK source is copied, vendored or forked into this repository.

The material dependency set resolved/reviewed for the SDK is permissively licensed. The reviewed licenses include MIT, BSD-3-Clause, Apache-2.0 and PSF-2.0 families. Representative direct dependencies include:

- `anyio` — MIT;
- `httpx2` — BSD-3-Clause;
- `mcp-types` — distributed from the same MIT-licensed MCP SDK project;
- `pydantic` — MIT;
- `starlette` — BSD-3-Clause;
- `python-multipart` — Apache-2.0;
- `sse-starlette` — BSD-3-Clause;
- `uvicorn` — BSD-3-Clause;
- `jsonschema` — MIT and already separately reviewed by this repository;
- `PyJWT` — MIT;
- `typing-extensions` — PSF-2.0;
- `typing-inspection` — MIT;
- `opentelemetry-api` — Apache-2.0.

The SDK's authentication dependency path can additionally resolve `cryptography` when installed by upstream requirements. Those packages remain external installed distributions with their own license metadata; this repository does not redistribute their source.

## Functional fit

The official SDK supplies the two transports required by #12 without making either transport canonical:

- stdio subprocess connections for local MCP servers;
- Streamable HTTP connections for deployed MCP servers.

The adapter uses the SDK's high-level `Client` for initialization, tool discovery and tool invocation. It deliberately does not rely on the legacy/deprecated MCP ping request for health; successful transport connection and protocol negotiation are used instead.

## Architecture fit

- [x] SDK imports exist only below `ai_multi_agent_platform.adapters`.
- [x] The existing core architecture test continues to forbid importing `mcp` from core modules.
- [x] `MCPServerConfig`, `MCPTool` and `MCPClient` remain platform-owned projections.
- [x] SDK/Pydantic result objects are converted to canonical JSON before returning from the adapter.
- [x] MCP server/session/transport identifiers never become canonical Task, Run, Agent, Tool Invocation, Node or Worker identities.
- [x] Removing the optional dependency leaves native capabilities and core imports functional.

## Security and data handling

The stdio transport can start a configured local process; the Streamable HTTP transport can make network requests to a configured MCP endpoint. These actions are adapter capabilities and must remain subject to platform policy and deployment configuration.

`MCPServerConfig.environment` may contain credentials or other sensitive values. The adapter passes them to the stdio transport but deliberately excludes them from canonical adapter metadata and persistent invocation audit events.

Tool arguments and returned output are not automatically copied into the default persistent invocation audit stream. The observer records identifiers, lifecycle state, policy/approval information, placement and namespaced provider metadata instead.

## Resource footprint

- no GPU requirement;
- no platform-owned daemon or mandatory port;
- stdio servers consume one subprocess while connected;
- Streamable HTTP uses normal client networking;
- no recurring paid service is introduced;
- all ongoing cost remains determined by the user's own MCP servers/infrastructure.

## Replaceability and exit strategy

`MCPPythonSDKClient` is only one implementation of the small platform-owned `MCPClient` protocol. A different MCP library or a platform-owned transport can replace it while leaving `CapabilityRegistry`, `CapabilityInvoker`, agents and task contracts unchanged.

Removal steps:

1. remove `mcp==2.1.1` from the optional and development dependency sets;
2. remove or replace `adapters/mcp_sdk.py`;
3. retain `adapters/mcp.py` if another implementation still satisfies `MCPClient`;
4. run native/core architecture tests to verify MCP remains optional.

No persisted-data migration is required because canonical audit events and capability records do not serialize SDK objects.

## Validation requirements

- [x] Real stdio server fixture exists using the official SDK.
- [x] Integration test launches the stdio server and invokes its tool through the canonical registry/invoker path.
- [x] Streamable HTTP is supported by the concrete client through endpoint configuration.
- [x] Ambiguous endpoint+command configurations are rejected.
- [x] MCP output schemas are projected into canonical capability schemas.
- [ ] Final repository CI is green on the merge-ready PR head.

A future SDK upgrade must be an explicit version change and must repeat license/provenance review plus the real transport integration test. No MCP SDK version is allowed to redefine canonical platform contracts.
