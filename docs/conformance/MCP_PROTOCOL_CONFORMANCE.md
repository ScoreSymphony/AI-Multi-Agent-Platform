# MCP protocol conformance

Issue #731 adds official Model Context Protocol wire-level conformance evidence without changing the canonical capability architecture owned by #12.

## Evidence dimensions

MCP compatibility is deliberately split into two independent questions:

| Dimension | Meaning | Evidence owner |
| --- | --- | --- |
| `protocol_conformant` | The named MCP revision/direction/transport profile passed the exact pinned official `modelcontextprotocol/conformance` scenarios. | #731 |
| `platform_conformant` | That exact MCP profile works through the platform's canonical CapabilityRegistry/CapabilityInvoker and #46 environment path. | #12 / #46 |

Neither dimension implies the other. A production MCP compatibility claim requires both to be true for the **same** protocol revision, direction and transport profile.

The combined report schema is:

```text
ai-multi-agent-platform/mcp-compatibility/v2
```

It is a per-profile matrix. Each row records at least:

- protocol revision;
- mode/direction;
- transport profile;
- exact #46 deployment profile when platform evidence exists;
- protocol-evidence status and `protocol_conformant`;
- platform-evidence status and `platform_conformant`;
- claim status (`claimed`, `not_claimed`, `unsupported`);
- compatibility result (`compatible`, `incompatible`, `incomplete`, `not_claimed`, `unsupported`);
- exact suite, adapter and SDK revisions where applicable.

There is deliberately no serialized top-level `compatible=true` shortcut. Compatibility is a property of one concrete profile. Evidence for different transports, directions or protocol revisions is never merged into one claim.

Missing protocol or platform evidence fails closed for a claimed profile. A profile with only one evidence dimension is reported as `incomplete`, not compatible.

## Pinned tracks

Exact pins live in `conformance/mcp/pins.json`.

### Stable claimed track

- protocol revision: `2025-11-25`;
- official suite: `v0.1.16`;
- exact suite commit: `21a9a2febd7100d7c17ac1021ee7f2ed9f66a1e0`;
- direction: client;
- transport profile: `streamable-http`;
- selected official scenarios: `initialize`, `tools_call`;
- runtime implementation under test: `MCPPythonSDKClient` using the pinned official Python SDK;
- status: claimed and gating.

The #46 `ENV-MCP` platform path now exercises this same `2025-11-25 / client / streamable-http` identity against a local Streamable-HTTP fixture before the official protocol and platform evidence can become one `compatible` matrix row.

This is intentionally a narrow tool-client claim. It does not claim every MCP extension, authorization profile or server behavior merely because the Python SDK supports additional features.

### Prerelease informational track

- protocol revision: `2026-07-28`;
- official suite package version at the reviewed commit: `0.2.0-alpha.11`;
- exact suite commit: `a983ba93c91e0bb31d0b6849eeb52f0ad1083107`;
- direction: client;
- transport profile: `streamable-http-stateless`;
- selected scenarios: `tools_call`, `request-metadata`;
- runtime implementation under test: explicit `MCPStatelessHTTPClient` behind the same platform-owned `MCPClient` seam;
- status: **implemented and tested, but not yet a production compatibility claim** while the official conformance line remains prerelease; CI is non-gating/informational.

The stateless adapter does not perform a legacy `initialize` handshake. Every request carries the `MCP-Protocol-Version` header and the required `io.modelcontextprotocol/*` `_meta` fields. An `Unsupported protocol version` (`-32022`) response is retried once only when the server advertises the configured revision as supported; absence of a common revision fails closed instead of silently falling back to an untested protocol.

The stable Python-SDK path remains the default MCP builder. The stateless implementation is explicitly opt-in through `build_mcp_stateless_provider`; introducing the newer protocol therefore does not silently change existing MCP deployments.

## Exact revision negotiation

A revision-specific stable compatibility profile supplies `protocol_revision="2025-11-25"` to `MCPServerConfig`. `MCPPythonSDKClient` then:

1. uses the legacy initialize-handshake path;
2. reads the negotiated revision from the connected SDK client;
3. requires it to equal the configured revision exactly before tool discovery/invocation is accepted;
4. fails closed on older, newer or malformed/missing negotiated revisions rather than silently claiming another revision.

Contract tests cover exact success plus incompatible older, newer and malformed values. The same focused regression coverage also proves that the client async context is exited on both success and rejected negotiation.

## STDIO versus claimed HTTP compatibility

The platform continues to exercise a real MCP STDIO server through the official Python SDK in `tests/test_mcp_sdk_transport.py`. That remains valuable platform/transport integration evidence.

It is **not** combined with the stable official Streamable-HTTP protocol evidence. The compatibility matrix exposes STDIO as `not_claimed`, rather than treating HTTP protocol evidence plus STDIO platform evidence as one compatibility result.

## Server direction

The platform does not currently expose a product MCP server surface. The compatibility matrix therefore records the server profile as `unsupported` rather than reporting a failed client implementation or silently omitting the direction.

If a platform MCP server surface is introduced later, its official server-suite profile and matching #46 platform path must be added before any server compatibility claim is published.

## Machine-readable and retained evidence

The normalized protocol report records:

- platform commit/release;
- adapter revision and SDK version;
- MCP protocol revision;
- official suite repository/version/commit;
- mode and transport profile;
- claimed/gating state;
- per-scenario result, exit code and diagnostic tail;
- SHA-256 digests of runner stdout/stderr;
- paths to the retained full runner stdout/stderr;
- timestamp and environment metadata.

For every scenario, `mcp_protocol_conformance.py` also retains the official suite's generated result directory alongside the normalized runner logs. This preserves detailed upstream `checks.json`, client `stdout.txt` and `stderr.txt` where the pinned runner emits them. GitHub Actions uploads the complete retained artifact directory for both stable and prerelease tracks.

This keeps the compact platform evidence easy to consume while preserving the upstream detail needed to audit a failure or distinguish an adapter regression from a conformance-runner change.

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

For the prerelease stateless track, use the exact `a983ba93c91e0bb31d0b6849eeb52f0ad1083107` checkout and run:

```bash
python scripts/ci/mcp_protocol_conformance.py \
  --track prerelease \
  --suite-root .upstream/mcp-conformance \
  --json-report mcp-protocol-2026-07-28.json \
  --strict
```

After dependencies/checkouts are present, either conformance run itself needs no hosted MCP provider or paid API service.

To combine the stable official protocol evidence with the profile-matched #46 MCP platform path:

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

- `mcp-protocol-2025-11-25`: stable claimed track; unexpected official-suite, exact-profile platform or matrix failures fail the job and prevent that compatibility claim;
- `mcp-protocol-2026-07-28-informational`: exact prerelease stateless track; it runs in strict mode so protocol regressions are visible, but the job is non-gating while the upstream suite/profile remains prerelease and unclaimed.

Both retain normalized machine-readable evidence and full scenario artifacts. The reference/native platform remains MCP-independent.
