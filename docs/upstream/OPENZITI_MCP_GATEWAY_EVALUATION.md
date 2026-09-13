# OpenZiti MCP Gateway private-MCP evaluation

Status: **candidate / live two-VPS evidence pending for issue #967**.

This document is the source, provenance, architecture and decision-readiness review for evaluating
OpenZiti MCP Gateway as an optional private remote MCP transport profile. It does **not** approve
OpenZiti, zrok or MCP Gateway as a canonical platform dependency and it does not record a final #967
recommendation.

## Candidate identity

- **Project:** `openziti/mcp-gateway`
- **Release:** `v0.1.11`
- **Pinned commit:** `8f99623d95d2f5223d2fa12b9f125688d8c80bf9`
- **Release date:** 2026-08-25
- **License:** Apache-2.0, verified from the pinned source on 2026-09-13
- **Build language/toolchain:** Go 1.25.7 at the pinned revision
- **Integration category:** optional external self-hosted service / adapter integration
- **Canonical platform boundary:** existing MCP provider + Connection + Capability + Authorization + SecretReference contracts

The pinned `go.mod` directly requires:

| Component | Version | Resolved revision | Verified license | Role in evaluation |
| --- | --- | --- | --- | --- |
| `openziti/mcp-gateway` | `v0.1.11` | `8f99623d95d2f5223d2fa12b9f125688d8c80bf9` | Apache-2.0 | gateway/bridge/client binaries |
| `openziti/zrok` | `v2.0.0-rc7` | `a325978114282cfb59d794f67c5d1e82956e816a` | Apache-2.0 | private-share/overlay service layer |
| `openziti/agora` | `v0.1.5` | `bb779c2c3d758e90276e86ef6d7d9e1dde5ab2ed` | Apache-2.0 | optional direct dependency; not required by the baseline zrok profile |
| `openziti/sdk-golang` | `v1.5.4` | `baf6808f09a4c23d6099ce82a677230e61917a4a` | Apache-2.0 | OpenZiti client SDK dependency |
| `modelcontextprotocol/go-sdk` | `v1.1.0` | version pin retained from `go.mod` | review with build SBOM | MCP protocol implementation dependency |

The exact OpenZiti controller/router images or packages used for the **self-hosted** live campaign must
be added to the measured report at execution time. They are deployment inputs, not inferred from a
moving zrok documentation example.

## Source-reviewed behavior at the pin

The pinned upstream documentation and configuration establish the following candidate behavior:

- `mcp-gateway` aggregates MCP backends and creates/serves a private zrok share;
- local `stdio` backends can be spawned directly by the gateway, so the backend itself needs no TCP
  listener;
- `mcp-tools run <share>` bridges a private remote share back to stdio;
- `mcp-tools http <share> --bind 127.0.0.1:<port>` exposes a **local loopback** Streamable HTTP endpoint
  for an HTTP MCP client;
- gateway backends are namespaced by backend ID, preventing silent name collisions;
- allow/deny tool filtering exists per backend;
- argument-aware path policy exists for colocated stdio backends;
- ephemeral zrok shares are closed/owner-only by default;
- named zrok accounts can be granted access when a new share is created;
- persistent/pre-created share tokens are a separate lifecycle path; upstream validation does not
  allow `zrok.share.access_grants` to be combined blindly with a pre-existing `share_token`.

That last distinction matters for #967: the isolation test and the restart/persistent-share test must
record the exact share lifecycle/ACL mechanism they exercise instead of assuming the two modes have
identical semantics.

## Platform architecture mapping

No new canonical overlay-network subsystem is justified by the current source review.

### Canonical ownership remains unchanged

- **#12 Capability** remains the authoritative inventory and invocation contract.
- **#15 Authorization/Approval** decides whether a principal may invoke a capability.
- **#34 Secrets** owns enrollment tokens, private identity material and share/service credentials via
  `SecretReference`-compatible handling.
- **#44 Connection** represents a persistent remote MCP connection when one is modeled canonically.
- MCP Gateway/zrok/OpenZiti IDs stay provider/deployment metadata.
- Gateway-side tool filtering/path policy is defense in depth only.

A valid overlay identity therefore proves only that the remote transport may be reached. It cannot
make an otherwise unauthorized `CapabilityInvocation` legal.

### Connection/profile projection

The preferred representation reuses the existing Connection seams:

- non-sensitive provider/profile identifiers in `endpoint_metadata`;
- raw share/enrollment/identity material only through `secret_references`;
- the MCP endpoint used by the platform remains a normal platform-owned `MCPServerConfig` target;
- for the baseline two-node PoC, Node A can run `mcp-tools http` on loopback and point the existing MCP
  HTTP client at that loopback endpoint.

No raw zrok token, enrollment token or private identity material belongs in Task, Run, Capability,
Connection telemetry or ordinary evidence JSON.

## Reference evaluation topology

```text
Node / VPS A
  Platform CapabilityInvocation
      -> #15 authorization/approval
      -> existing MCP provider
      -> 127.0.0.1:<client-port> (mcp-tools http)
      -> authenticated private zrok/OpenZiti overlay

Node / VPS B
  mcp-gateway
      -> local stdio MCP fixture/backend
      -> bounded evaluation workspace
```

The primary profile deliberately uses a local stdio backend on Node B. This gives the strongest
backend-exposure proof: the backend has **no network listener at all**. Any OpenZiti/zrok controller,
router or management ports needed for the overlay are captured separately and are never described as
MCP backend exposure.

## Required live evidence

The versioned evidence contract is:

`src/ai_multi_agent_platform/benchmarking/schemas/private-mcp-transport-evaluation-report.v1.schema.json`

and its readiness implementation is:

`ai_multi_agent_platform.benchmarking.private_mcp_transport_evaluation`.

A report cannot become decision-ready unless it retains evidence for all mandatory #967 cases,
including:

- authorized principal + valid transport identity succeeds;
- unauthorized principal + valid transport identity remains denied;
- invalid/revoked transport identity fails closed;
- cross-client/service isolation and provider-ID non-authority;
- canonical capability allowlist authority, filter non-widening, collision handling and dynamic
  backend non-escalation;
- gateway/backend/client restart, partition/reconnect, Node B loss and no duplicate side effect;
- unrelated services unreachable and no unrestricted-tunnel behavior;
- self-hosted/no-new-recurring-paid-service operation and local MCP independence;
- direct-local, warm private-overlay, connection-establishment and reconnect latency distributions.

The readiness helper additionally rejects decision evidence if the backend is public, transport
identity is not separated from authorization, plaintext credentials leak into evidence, the
self-hosted path is absent, or the evaluated production path requires a new recurring paid service.

## Secret and evidence handling

The live campaign must treat all of these as secrets or secret-adjacent provider material:

- zrok enable/enrollment tokens;
- private OpenZiti/zrok identity material;
- share/service tokens;
- management/API tokens where used.

Raw values are never checked into `tests/evidence/issue_967`, copied into the report, or emitted into
ordinary CI logs. Evidence may retain stable hashes/fingerprints only where that is sufficient to
prove rotation/revocation without disclosing the credential.

## Self-hosting and cost boundary

Hosted zrok is useful for a quick compatibility comparison but is **not sufficient** for the #967
cost acceptance criterion. The decision-eligible campaign must either:

1. run the useful profile against a self-hosted zrok/OpenZiti deployment using already-covered VPS
   resources and record the exact controller/router/service versions and exposed infrastructure
   ports; or
2. record `reject/defer` if the required production behavior cannot be obtained without an additional
   recurring paid dependency.

The baseline platform remains fully functional without this candidate.

## Comparison baseline

The final live report and decision record must compare the same representative tool workload across:

| Area | Public remote MCP | Simpler private route (SSH/VPN-style) | OpenZiti MCP Gateway profile |
| --- | --- | --- | --- |
| Public MCP backend port required | measure/configure | measure/configure | expected no; prove live |
| Per-service identity | measure | measure | candidate capability; prove live |
| Revocation | measure | measure | prove live |
| Canonical Capability fit | same #12 authority | same #12 authority | same #12 authority |
| Canonical authorization | same #15 authority | same #15 authority | same #15 authority |
| Secret handling | #34 | #34 | #34 + provider identity material |
| Restart/reconnect | measure | measure | measure |
| Added latency | measure | measure | measure p50/p95 |
| Self-hosted cost fit | profile-dependent | expected compatible | must be proven |
| Lateral-movement surface | highest if broadly exposed | topology-dependent | per-service claim must be proven |
| Operational complexity | baseline | compare | compare |

The evaluation must not prefer OpenZiti merely because it is more sophisticated. If a simpler
private route provides equivalent service isolation, revocation, cost and recovery with materially
lower operational burden, `prefer_simpler_private_networking` is the correct result.

## Current decision state

No final recommendation is recorded yet. The only valid final values are:

- `adopt_reference_private_mcp_profile`
- `experimental_only`
- `prefer_simpler_private_networking`
- `reject/defer`

Source/provenance review is complete enough to begin the live campaign. The final recommendation and
issue closure remain blocked on reproducible two-VPS evidence, including the self-hosted path.
