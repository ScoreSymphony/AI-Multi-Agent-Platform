# MCP protocol conformance

Issue #731 adds official Model Context Protocol wire-level conformance evidence without changing the canonical capability architecture owned by #12.

## Evidence dimensions

MCP compatibility is deliberately split into two independent questions:

| Dimension | Meaning | Evidence owner |
| --- | --- | --- |
| `protocol_conformant` | The claimed MCP client/server profile passed the exact pinned official `modelcontextprotocol/conformance` scenarios for the named protocol revision. | #731 |
| `platform_conformant` | The MCP implementation works through the platform's canonical CapabilityRegistry/CapabilityInvoker, policy, traceability and #46 environment path. | #12 / #46 |

Neither dimension implies the other. A production MCP compatibility claim requires both to be true for the same claimed profile.

The combined report schema is:

```text
ai-multi-agent-platform/mcp-compatibility/v1
```

Missing protocol evidence is represented explicitly with `protocol_evidence_status="missing"` and `protocol_conformant=null`; it never becomes an implicit compatibility claim.

## Pinned tracks

Exact pins live in `conformance/mcp/pins.json`.

### Stable claimed track

- protocol revision: `2025-11-25`;
- official suite: `v0.1.16`;
- exact suite commit: `21a9a2febd7100d7c17ac1021ee7f2ed9f66a1e0`;
- direction: client;
- official runner transport: Streamable HTTP/HTTP test server;
- currently selected scenarios: `initialize`, `tools_call`;
- status: claimed and gating.

This is intentionally a narrow tool-client claim. It does not claim every MCP extension, authorization profile or server behavior merely because the Python SDK supports additional features.

### Prerelease informational track

- protocol revision: `2026-07-28`;
- official suite package version at the reviewed commit: `0.2.0-alpha.11`;
- exact suite commit: `a983ba93c91e0bb31d0b6849eeb52f0ad1083107`;
- direction: client;
- transport profile: stateless HTTP;
- selected probe scenarios: `tools_call`, `request-metadata`;
- status: **not claimed**, non-gating/informational.

The current platform MCP adapter is stateful through the pinned Python SDK abstraction and does not yet claim the newer stateless request semantics. The conformance harness therefore rejects `2026-07-28` explicitly rather than silently negotiating or reporting support. A future change may promote this track only after the adapter implementation and official suite evidence both pass.

## STDIO versus official protocol evidence

The platform already exercises a real MCP STDIO server through the official Python SDK in `tests/test_mcp_sdk_transport.py`. That remains valuable `platform_conformant` evidence for the configured transport.

It is **not** relabeled as official wire-protocol conformance: the official client conformance runner used by #731 launches its own HTTP test server and observes protocol behavior there. Evidence must state the transport/profile it actually tested.

## Server direction

The platform does not currently expose a product MCP server surface, so no server-conformance profile is claimed. This is `unsupported/not claimed`, not a failed client implementation. If a platform MCP server surface is introduced later, its official server-suite profile must be added before any server compatibility claim is published.

## Local execution

The runner never downloads an uncontrolled `latest` package. Obtain the exact upstream checkout named by the selected track and build it once from its lockfile:

```bash
git clone https://github.com/modelcontextprotocol/conformance .upstream/mcp-conformance
git -C .upstream/mcp-conformance checkout 21a9a2febd7100d7c17ac1021ee7f2ed9f66a1e0
npm --prefix .upstream/mcp-conformance ci --no-audit --no-fund
npm --prefix .upstream/mcp-conformance run build
python scripts/ci/mcp_protocol_conformance.py \
  --track stable \
  --suite-root .upstream/mcp-conformance \
  --json-report mcp-protocol-2025-11-25.json
```

After dependencies/checkouts are present, the conformance run itself needs no hosted MCP provider or paid API service.

To combine the official protocol evidence with the existing #46 MCP platform path:

```bash
python scripts/ci/issue46_optional_environment_profile.py \
  mcp \
  --protocol-evidence mcp-protocol-2025-11-25.json \
  --json-report conformance-mcp-platform.json \
  --compatibility-report mcp-compatibility-2025-11-25.json
```

## Pin/update discipline

The runner verifies both the suite package version and exact Git commit before executing any scenario. A mismatch fails before protocol tests start.

Updating the suite requires an explicit upstream-review PR under #42 discipline:

1. review suite release/commit and license changes;
2. inspect protocol/scenario/runner changes;
3. update `conformance/mcp/pins.json` and provenance records;
4. run the old/new evidence paths separately;
5. investigate changed results rather than rewriting the compatibility claim automatically;
6. promote a formerly informational track to gating only through an explicit reviewed change.

## CI

`.github/workflows/mcp-protocol-conformance.yml` has two tracks:

- `mcp-protocol-2025-11-25`: stable claimed track; unexpected scenario failures fail the job and prevent that compatibility claim;
- `mcp-protocol-2026-07-28-informational`: exact prerelease probe; non-gating while the revision remains unclaimed.

Both retain machine-readable artifacts. The reference/native platform remains MCP-independent.
