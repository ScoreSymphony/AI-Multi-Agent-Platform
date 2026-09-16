# Optional environment conformance profiles

The platform retains evidence for optional environment-dependent paths in addition to the reference/native baseline. These checks are environment-matrix adjuncts rather than new A–X product scenarios.

## MCP (`ENV-MCP`)

The MCP platform profile requires the exact optional dependency `mcp==2.2.0`. It starts the maintained real stdio fixture through the official Model Context Protocol Python SDK and invokes `tool.lookup` through the same canonical `CapabilityRegistry` and `CapabilityInvoker` path used by native capabilities.

The profile fails closed when the MCP distribution is absent or its installed version differs from the repository pin. A passing run produces a normal `ai-multi-agent-platform/platform-conformance/v1` report with deployment profile `mcp-sdk-pinned` and records `mcp-python-sdk=2.2.0` in adapter versions.

This `ENV-MCP` result is **platform integration evidence**, not wire-level protocol certification. Issue #731 adds a separate official-suite evidence path documented in [`MCP_PROTOCOL_CONFORMANCE.md`](MCP_PROTOCOL_CONFORMANCE.md). For a claimed MCP profile, CI combines both dimensions into `ai-multi-agent-platform/mcp-compatibility/v1`:

- `protocol_conformant` — exact pinned official `modelcontextprotocol/conformance` result for the named protocol revision/profile;
- `platform_conformant` — the canonical CapabilityRegistry/CapabilityInvoker environment path.

Neither dimension implies the other. Missing official protocol evidence is reported explicitly and cannot become a compatibility claim.

The stable #731 workflow retains three artifacts for the claimed `2025-11-25` tool-client profile:

- `mcp-protocol-2025-11-25.json` — official protocol evidence;
- `conformance-mcp-platform.json` — platform integration evidence;
- `mcp-compatibility-2025-11-25.json` — combined claim state with both dimensions preserved.

The existing required `test` job may continue to run `ENV-MCP` independently as a regression for the canonical platform path. This does not make MCP a production dependency: the normal package dependencies remain MCP-free and the SDK stays in the optional `mcp` extra (also present in the development test environment).

## LiteLLM (`ENV-LITELLM`)

The LiteLLM profile requires the exact optional dependency `litellm==1.100.1`. It runs the repository's pinned real-library integration path. LiteLLM's supported `mock_response` mechanism exercises the installed library's request/response machinery without a paid provider, network credential or external model service, while the platform adapter preserves the canonical `ModelProvider` request/response boundary.

The profile fails closed when LiteLLM is absent or its installed version differs from the repository pin. A passing run produces the same versioned conformance report with deployment profile `litellm-pinned` and records `litellm=1.100.1` in adapter versions.

The existing required `litellm-compat` CI context runs this profile and retains `platform-conformance-litellm` / `conformance-litellm.json`.

## Real distributed infrastructure (`ENV-DISTRIBUTED-REAL`)

Issue #562 defines the repository-side topology, acceptance harness and evidence contracts for a physically real two-VPS deployment connected through a private tunnel. Issue #829 owns the actual execution on independent operator-controlled VPS/reference hosts and retention of the resulting sanitized live evidence.

This is deliberately **not** the same evidence as optional Scenario E in the normal integration/release matrix: Scenario E exercises the canonical distributed Worker contracts with maintained fixtures, while `ENV-DISTRIBUTED-REAL` can only become a passing claim after #829 has exercised those contracts across two independent hosts and a real network boundary.

The #829 live operator flow uses the runbook prepared under #562 in [`../operations/TWO_VPS_PRIVATE_TUNNEL_ACCEPTANCE.md`](../operations/TWO_VPS_PRIVATE_TUNNEL_ACCEPTANCE.md). It produces the finalized sanitized report using the stable `issue562-*` schema lineage introduced by #562. That schema name is retained for compatibility/provenance and does not assign physical-host execution ownership back to #562.

The retained live report can then be promoted into the standard platform conformance report with:

```text
python scripts/ci/real_two_vps_conformance.py \
  --acceptance-evidence issue562-two-vps-private-tunnel.json \
  --json-report conformance-real-two-vps.json
```

The bridge validates the retained compatibility schema, all required live phases, private/public network probes, canonical IDs, advertised Worker capabilities, secret/address sanitization and the real-infrastructure conformance marker. It also requires the `platform_commit` in the live report to equal the exact Git commit of the checkout making the compatibility claim. Evidence from another commit therefore cannot silently certify a newer or different platform revision.

If the command is run without `--acceptance-evidence`, the generated `ai-multi-agent-platform/platform-conformance/v1` report records `ENV-DISTRIBUTED-REAL` as `required=true`, `status=unsupported`, `compatibility_result=not_claimed` and the overall real-infrastructure claim as `incomplete`. Simulated Scenario E evidence is never substituted for the missing live report. When valid #829 live evidence is supplied, the scenario becomes `pass` and promotes the observed Node/Worker/Task/Run/WorkerJob IDs plus sanitized evidence references into the standard conformance report.

This profile is an operator/release acceptance path and is not part of the fast PR suite. The repository-side preparation is completed under #562; the physical-host campaign remains tracked under #829. It requires no paid AI/API/workflow service beyond the already operated VPS infrastructure.

## Boundary

None of these profiles changes canonical lifecycle ownership or the reference single-node compatibility claim. Their reports make only the exact optional environment claim they actually execute. The reference-only platform must continue to install and operate without these optional environments, while releases or deployments that claim them have machine-readable evidence tied to the tested platform commit and relevant environment/component identity.
