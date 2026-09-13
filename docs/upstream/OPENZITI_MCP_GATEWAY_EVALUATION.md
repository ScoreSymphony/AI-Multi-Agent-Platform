# OpenZiti MCP Gateway private-MCP evaluation

Status: **candidate / live two-VPS evidence pending for issue #967**.

This document reviews OpenZiti MCP Gateway as an optional private remote MCP transport profile. It
does **not** approve OpenZiti, zrok or MCP Gateway as a canonical platform dependency and does not
record a final #967 recommendation.

## Candidate identity

- **Project:** `openziti/mcp-gateway`
- **Release:** `v0.1.11`
- **Pinned commit:** `8f99623d95d2f5223d2fa12b9f125688d8c80bf9`
- **Release date:** 2026-08-25
- **License:** Apache-2.0, verified from pinned source on 2026-09-13
- **Build toolchain:** Go 1.25.7 at the pin
- **Integration category:** optional external self-hosted service / adapter integration
- **Platform boundary:** existing MCP provider + Connection + Capability + Authorization +
  SecretReference contracts

Pinned direct dependency boundary:

| Component | Version | Resolved revision | License | Evaluation role |
| --- | --- | --- | --- | --- |
| `openziti/mcp-gateway` | `v0.1.11` | `8f99623d95d2f5223d2fa12b9f125688d8c80bf9` | Apache-2.0 | gateway/bridge/client binaries |
| `openziti/zrok` | `v2.0.0-rc7` | `a325978114282cfb59d794f67c5d1e82956e816a` | Apache-2.0 | private-share/overlay layer |
| `openziti/agora` | `v0.1.5` | `bb779c2c3d758e90276e86ef6d7d9e1dde5ab2ed` | Apache-2.0 | optional direct dependency |
| `openziti/sdk-golang` | `v1.5.4` | `baf6808f09a4c23d6099ce82a677230e61917a4a` | Apache-2.0 | OpenZiti client SDK |
| `modelcontextprotocol/go-sdk` | `v1.1.0` | version pin from `go.mod` | verify with build SBOM | MCP protocol dependency |

The exact OpenZiti controller/router images or packages used by the self-hosted campaign are measured
deployment inputs. They must be pinned in the live report rather than inferred from moving docs.

## Source-reviewed behavior at the pin

The pinned source/configuration establishes that:

- `mcp-gateway` aggregates MCP backends and can serve a private zrok share;
- local stdio backends can be spawned directly, so the backend itself needs no TCP listener;
- `mcp-tools run <share>` bridges a remote private share to stdio;
- `mcp-tools http <share> --bind 127.0.0.1:<port>` exposes a local loopback Streamable HTTP endpoint;
- backend tool names are namespaced by backend ID;
- allow/deny filtering exists per backend;
- argument-aware path policy exists for colocated stdio backends;
- ephemeral zrok shares are closed/owner-only by default;
- named zrok accounts can be granted access when a new share is created;
- pre-created/persistent shares use a separate lifecycle and upstream validation does not allow
  `zrok.share.access_grants` to be blindly combined with an existing `share_token`.

### Material source-review risk: share token in process argv

At `v0.1.11`, zrok-mode `mcp-tools run/http` resolves the share target from a positional command-line
argument. Without Agora, the implementation requires exactly one positional share token; the zrok CLI
path does not expose an environment/file/stdin input for that value.

This matters because #34 `SecretReference` handling only protects how the platform stores and resolves
the credential. It does not by itself prevent the resolved value from becoming observable in a child
process command line. A production-supportable profile therefore has to prove one of the following:

1. the resolved credential is not observable through process argv under the supported deployment;
2. a separately pinned mitigation removes the argv exposure without weakening the rest of the
   security model; or
3. the candidate is not adopted as a supported production profile.

The versioned readiness contract now records `process_argv_secret_exposure_detected` explicitly and
blocks decision-ready adoption evidence when it is true.

## Architecture mapping

No new canonical overlay-network subsystem is justified.

Canonical ownership remains unchanged:

- **#12 Capability** owns inventory/invocation contracts;
- **#15 Authorization/Approval** decides whether a principal may invoke a capability;
- **#34 Secrets** owns enrollment/share/identity material through `SecretReference` handling;
- **#44 Connection** represents a persistent remote connection when modeled canonically;
- MCP Gateway/zrok/OpenZiti identifiers remain provider/deployment metadata;
- gateway filtering/path policy is defense in depth only.

A valid overlay identity proves transport reachability only. It cannot make an unauthorized
`CapabilityInvocation` legal.

### Connection/profile projection

Reuse the existing seams:

- non-sensitive provider/profile identifiers in `endpoint_metadata`;
- secret material through `secret_references`;
- normal platform-owned `MCPServerConfig` for the local MCP endpoint used by the platform;
- Node A may use a loopback bridge when its credential-delivery mechanism passes the secret-handling
  gate.

No raw zrok token, enrollment token or private identity material belongs in Task, Run, Capability,
Connection telemetry or ordinary evidence JSON.

## Reference topology

```text
Node / VPS A
  Platform CapabilityInvocation
      -> #15 authorization/approval
      -> existing MCP provider
      -> 127.0.0.1:<client-port> (private transport client/bridge)
      -> authenticated zrok/OpenZiti overlay

Node / VPS B
  mcp-gateway
      -> local stdio MCP fixture/backend
      -> bounded evaluation workspace
```

Using a stdio backend on Node B gives a strong exposure proof: the actual MCP backend has no network
listener. OpenZiti/zrok controller/router/management ports are captured separately and never described
as MCP backend exposure.

## Required live evidence

Evidence contract:

`src/ai_multi_agent_platform/benchmarking/schemas/private-mcp-transport-evaluation-report.v1.schema.json`

Readiness implementation:

`ai_multi_agent_platform.benchmarking.private_mcp_transport_evaluation`

Decision readiness requires retained evidence for:

- authorized principal + valid transport succeeds;
- unauthorized principal + valid transport remains denied;
- invalid/revoked transport fails closed;
- revocation while a transport/session is already established, including proof that stale access does
  not survive beyond the documented accepted effective-revocation window;
- service removal/reconfiguration, including fail-closed behavior for the old path and explicit
  recovery behavior for the intended replacement/restored path;
- cross-client/service isolation and provider-ID non-authority;
- canonical capability allowlist authority, filter non-widening, collision handling and dynamic
  backend non-escalation;
- gateway/backend/client restart, partition/reconnect, Node B loss and no duplicate side effect;
- unrelated services unreachable and no unrestricted-tunnel behavior;
- self-hosted/no-new-recurring-paid-service operation and local MCP independence;
- direct-local, warm private-overlay, connection-establishment and reconnect latency distributions;
- no plaintext secret leakage in retained state/log evidence;
- no resolved credential exposure through process argv.

The helper also rejects evidence when the MCP backend is public, transport identity is not separated
from authorization, the self-hosted path is absent or the evaluated production path requires a new
recurring paid service.

## Secret/evidence handling

Treat as secret or secret-adjacent:

- zrok enable/enrollment tokens;
- private OpenZiti/zrok identity material;
- share/service tokens;
- management/API tokens.

Raw values are never checked into `tests/evidence/issue_967`, copied into the report or emitted into
ordinary CI logs. Evidence may retain hashes/fingerprints where sufficient. The live campaign must
also inspect process argv because the pinned zrok CLI currently takes the share token positionally.

## Self-hosting and cost boundary

Hosted zrok can provide compatibility evidence but is insufficient for the #967 cost criterion. A
decision-eligible campaign must either:

1. run against self-hosted/already-covered zrok/OpenZiti infrastructure and record exact versions,
   state and exposed infrastructure ports; or
2. record `reject/defer` when useful production behavior requires a new recurring paid dependency.

The baseline platform remains functional without this candidate.

## Comparison baseline

The final campaign compares the same representative workload across public remote MCP, a simpler
private route and the OpenZiti/MCP Gateway candidate.

| Area | Public remote MCP | Simpler private route | OpenZiti MCP Gateway |
| --- | --- | --- | --- |
| Public MCP backend port | measure | measure | expected none; prove |
| Per-service identity | measure | measure | prove |
| Revocation | measure | measure | prove |
| Canonical Capability | #12 | #12 | #12 |
| Canonical authorization | #15 | #15 | #15 |
| Secret delivery surface | measure | measure | includes positional-token risk |
| Restart/reconnect | measure | measure | measure |
| Added latency | measure | measure | p50/p95 |
| Self-hosted cost fit | profile-dependent | expected compatible | prove |
| Lateral movement | highest if broad exposure | topology-dependent | prove per-service boundary |
| Operational complexity | baseline | compare | compare |

The evaluation must not prefer OpenZiti because it is more sophisticated. If a simpler private route
provides equivalent isolation, revocation, credential hygiene, cost and recovery with materially lower
operational burden, `prefer_simpler_private_networking` is the appropriate result.

## Current decision state

No final recommendation is recorded. Allowed final values are:

- `adopt_reference_private_mcp_profile`
- `experimental_only`
- `prefer_simpler_private_networking`
- `reject/defer`

Source/provenance review is complete enough to begin the live campaign. The positional share-token
behavior is a known adoption risk, and issue closure remains blocked on reproducible two-VPS evidence,
the self-hosted path, credential-delivery evidence and one final recommendation.