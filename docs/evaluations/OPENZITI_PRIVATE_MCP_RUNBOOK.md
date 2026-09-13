# OpenZiti MCP Gateway two-node evidence runbook

This runbook produces the live evidence required by issue #967. It is intentionally stricter than a
simple connectivity demo: a successful tool call is not enough. The campaign must also prove
canonical authorization separation, non-public backend exposure, revocation/failure behavior,
idempotency, lateral-movement limits, self-hosted cost compatibility and repeatable latency.

## 1. Pinned campaign inputs

Use these source inputs unless a later #967 PR explicitly updates the pin and repeats the review:

- `openziti/mcp-gateway` `v0.1.11`
  - commit `8f99623d95d2f5223d2fa12b9f125688d8c80bf9`
  - Apache-2.0
  - Go 1.25.7
- `openziti/zrok/v2` `v2.0.0-rc7`
  - commit `a325978114282cfb59d794f67c5d1e82956e816a`
  - Apache-2.0
- `openziti/sdk-golang` `v1.5.4`
  - commit `baf6808f09a4c23d6099ce82a677230e61917a4a`
  - Apache-2.0

Record the exact self-hosted OpenZiti controller/router images or packages used by the campaign in the
final report. Do not substitute `latest` tags in decision evidence.

## 2. Evidence directory

Create one immutable run directory per campaign execution:

```text
tests/evidence/issue_967/
└── <run-id>/
    ├── report.json
    ├── environment/
    ├── network/
    ├── authorization/
    ├── capabilities/
    ├── recovery/
    ├── lateral-movement/
    ├── latency/
    └── hashes.sha256
```

The checked-in `report.json` must validate against:

`src/ai_multi_agent_platform/benchmarking/schemas/private-mcp-transport-evaluation-report.v1.schema.json`.

Never retain raw share tokens, enrollment tokens, private identities, cookies, API tokens or other
credential material. Evidence references point to redacted artifacts; `raw_evidence` stores only
paths and SHA-256 hashes.

## 3. Required topology

Use two distinct hosts/VPS instances in the same region where practical.

### Node A — platform/client side

- platform Control Plane/Worker or the canonical invocation harness;
- existing #15 authorization/approval active;
- existing #34 secret handling active;
- `mcp-tools` from the pinned `mcp-gateway` source;
- local MCP proxy bound to loopback only, e.g. `127.0.0.1:18080`;
- no direct route to a public MCP backend port on Node B.

### Node B — gateway/backend side

- `mcp-gateway` from the pinned source;
- bundled `mcp-filesystem` as the deterministic test backend;
- backend launched as a local stdio child process;
- bounded workspace `/var/lib/ai-multi-agent-platform/issue967/workspace`;
- no MCP backend TCP listener;
- only the overlay infrastructure reachability required by the exact self-hosted zrok/OpenZiti
  deployment.

Use `deploy/distributed/openziti-private-mcp-evaluation/gateway.example.yml` as the baseline Node B
configuration.

## 4. Build the pinned MCP Gateway binaries

On both nodes, obtain the exact upstream source through the normal operator-controlled source-fetch
process, verify the checkout is exactly the pinned commit, and build only from that checkout.

Representative commands after source retrieval:

```bash
git checkout --detach 8f99623d95d2f5223d2fa12b9f125688d8c80bf9
test "$(git rev-parse HEAD)" = "8f99623d95d2f5223d2fa12b9f125688d8c80bf9"
mkdir -p ./bin
go build -o ./bin/mcp-gateway ./cmd/mcp-gateway
go build -o ./bin/mcp-tools ./cmd/mcp-tools
go build -o ./bin/mcp-filesystem ./cmd/mcp-filesystem
sha256sum ./bin/mcp-gateway ./bin/mcp-tools ./bin/mcp-filesystem
```

Retain source commit, Go version and binary hashes. Do not treat a binary hash from another machine as
proof of byte-for-byte reproducibility unless reproducibility is independently established.

## 5. Prepare the self-hosted overlay

Decision-eligible evidence requires a self-hosted or otherwise already-covered zrok/OpenZiti path.
The exact deployment mechanism may be Docker/Compose, system services or another maintained upstream
profile, but the run must record:

- zrok version/revision;
- OpenZiti controller version/image digest;
- OpenZiti router version/image digest;
- database/message-bus components actually required by that profile;
- public/private controller/router/frontend ports;
- DNS/TLS endpoints;
- state volumes;
- identity enrollment/bootstrap procedure;
- restart/backup implications.

The report must explicitly distinguish those overlay/control-plane ports from the MCP backend. The
backend on Node B remains a stdio process with no network listener.

Hosted zrok may be run as a separate compatibility comparison, but a hosted-only result must set
`topology.self_hosted_overlay=false` and is not decision-ready.

## 6. Enroll Node A and Node B without leaking credentials

Enroll each node/environment according to the pinned self-hosted zrok profile. Treat all enable or
enrollment tokens and generated private identity material as #34-owned secrets.

For the baseline owner-only share profile, Node A and Node B may use separately enrolled environments
under the same authorized zrok account. For the cross-client isolation case, enroll a second account
or identity that is not entitled to the private share and prove that it cannot connect.

If the campaign uses explicit access grants, record the exact ACL mechanism. At the pinned
`mcp-gateway` revision, `zrok.share.access_grants` applies to newly created shares and must not be
assumed to combine with a pre-existing `share_token`.

## 7. Start Node B

Prepare the bounded fixture directory and install the pinned binary at the path referenced by the
profile:

```bash
sudo install -d -m 0700 /var/lib/ai-multi-agent-platform/issue967/workspace
sudo install -d -m 0755 /opt/ai-multi-agent-platform/issue967/bin
sudo install -m 0755 ./bin/mcp-filesystem /opt/ai-multi-agent-platform/issue967/bin/mcp-filesystem
```

Start the pinned gateway with the checked-in evaluation profile:

```bash
./bin/mcp-gateway run \
  /path/to/AI-Multi-Agent-Platform/deploy/distributed/openziti-private-mcp-evaluation/gateway.example.yml
```

Standalone mode emits a share token. Treat the startup channel containing that token as secret
material: ingest the token into the test environment's #34-compatible secret flow and do not retain
its raw value in logs/evidence.

## 8. Prove the backend is not public

On Node B, retain redacted listener/process evidence showing:

1. the `mcp-filesystem` backend exists only as a child stdio process;
2. there is no backend TCP listener bound to `0.0.0.0`, `::`, the public interface or another network
   interface;
3. any listeners belonging to zrok/OpenZiti are labelled separately.

From an external host that is **not** enrolled in the overlay, scan only the explicitly authorized
campaign target/port set and retain evidence that no MCP backend endpoint is reachable. Do not broaden
this into an uncontrolled scan.

A successful overlay call does not substitute for this negative exposure proof.

## 9. Start Node A loopback bridge

Resolve the private share credential through the test secret flow and launch:

```bash
./bin/mcp-tools http "$ISSUE967_SHARE_TOKEN" --bind 127.0.0.1:18080
```

Verify that the process listens only on loopback. Configure the existing platform MCP HTTP adapter as:

```python
MCPServerConfig(
    server_id="issue-967-private",
    endpoint="http://127.0.0.1:18080",
)
```

The share token stays outside `MCPServerConfig` and outside canonical Task/Run/Capability state.

## 10. Happy-path tools

Exercise two fixture operations through the normal canonical capability path:

- **read-only:** `fixture:list_directory` or `fixture:read_file`;
- **bounded side effect:** `fixture:write_file` into the dedicated issue-967 workspace.

The write test must use a deterministic idempotency key/canonical invocation identity and a unique
fixture path so duplicate side effects can be detected after reconnect/retry.

Do not call the gateway directly when collecting canonical authorization evidence. Direct provider
calls are allowed only for isolated transport diagnostics and must be labelled as such.

## 11. Identity versus canonical authorization matrix

Retain one evidence artifact for each case ID used by the report:

| Case ID | Procedure | Required outcome |
| --- | --- | --- |
| `authorized_valid_transport` | authorized platform principal + valid overlay identity | invocation succeeds |
| `unauthorized_valid_transport_denied` | deny principal at #15 while transport remains valid | platform denies before provider execution |
| `invalid_or_revoked_transport_fails_closed` | revoke/disable transport identity or share access | transport cannot invoke backend |
| `cross_client_service_isolation` | unrelated enrolled account/identity attempts the private service | access denied |
| `provider_identifier_not_authority` | provide/know share/service identifiers without valid entitlement | access still denied |

Capture platform authorization decision evidence separately from overlay connection evidence. The
same artifact must never imply that successful transport authentication grants capability authority.

## 12. Capability/filtering matrix

| Case ID | Required proof |
| --- | --- |
| `canonical_allowlist_authoritative` | Agents see only canonical #12 capabilities, not arbitrary gateway inventory |
| `gateway_filter_cannot_widen` | loosening gateway allow/deny config does not make a #12/#15-denied tool invocable |
| `tool_collision_deterministic` | two backends exposing the same original tool name remain deterministically namespaced/mapped |
| `dynamic_backend_no_automatic_grant` | adding a backend/tool does not silently create an authorized canonical capability |

Gateway filters/path policy are useful defense in depth, but passing these cases requires platform
policy to remain authoritative when provider configuration changes.

## 13. Secret rotation and revocation

Exercise at least:

- share/service credential rotation where supported by the selected profile;
- overlay environment/identity revocation;
- restart persistence for the selected persistent-share lifecycle;
- absence of raw credentials in Connection serialization, Task/Run state, logs and report artifacts.

If a persistent share is used for restart evidence, document exactly how its ACL is managed. Do not
copy the ephemeral `access_grants` assumption onto a pre-created share.

## 14. Failure/recovery matrix

Run and retain all cases:

- `gateway_restart`
- `backend_restart`
- `client_restart`
- `network_partition_reconnect`
- `node_b_unavailable_before_invocation`
- `node_b_lost_during_invocation`
- `no_duplicate_side_effect_after_retry`

Additionally retain diagnostics for gateway-up/backend-down, invalid identity, malformed backend
response and overlay control-plane/DNS dependency outage where practical.

For the bounded write fixture, verify the final filesystem state and canonical invocation/run history
show one logical side effect after reconnect/retry. A new transport session must not silently create a
new canonical Run.

## 15. Lateral-movement boundary

Retain evidence for:

- `unrelated_node_b_services_unreachable`: the MCP service identity/path cannot reach an unrelated test
  listener on Node B;
- `gateway_not_unrestricted_tunnel`: no gateway/provider configuration used by the supported profile
  exposes arbitrary host:port proxying.

Also inspect provider management APIs separately from the agent-facing path. MCP tool metadata and
responses are untrusted data and must not be able to reconfigure transport/authorization policy.

## 16. Cost and local-baseline cases

Retain:

- `self_hosted_no_new_recurring_paid_service`: useful two-node profile runs using only already-covered
  infrastructure/software with no new recurring paid service;
- `local_mcp_without_overlay`: existing local MCP tests remain green with OpenZiti/zrok absent.

If the first case cannot pass, the baseline decision cannot be adoption.

## 17. Latency measurements

Use the same deterministic read-only tool and comparable payload where possible. Retain at least five
post-warmup observations per required distribution; more are preferred for stable p50/p95 values.

The v1 report requires:

- `direct_local`: local MCP fixture baseline;
- `private_overlay_warm`: warm remote private-overlay calls;
- `connection_establishment`: first usable connection/session establishment;
- `reconnect`: recovery-to-first-success after an induced interruption.

Record raw samples as evidence and compute p50/p95 from those samples. Do not present one hosted/VPS
run as a universal capacity claim.

## 18. Comparison with simpler private networking

Run an equivalent workload over the simplest already-available private route that is credible for the
same two nodes (for example an existing SSH/WireGuard/Tailscale-style route if already available and
cost-compatible). The comparison must cover:

- public backend exposure;
- identity granularity and revocation;
- lateral movement;
- operational steps/state;
- restart/recovery;
- p50/p95 overhead;
- incremental recurring cost.

The purpose is to answer whether MCP Gateway adds enough value to justify its operational surface,
not to maximize sophistication.

## 19. Build and validate the report

Populate one measured report only after the evidence files exist and have stable SHA-256 hashes.
Validate it with the repository helper in the project environment:

```python
import json
from pathlib import Path

from ai_multi_agent_platform.benchmarking.private_mcp_transport_evaluation import (
    assess_private_mcp_transport_evaluation,
)

report = json.loads(Path("tests/evidence/issue_967/<run-id>/report.json").read_text())
readiness = assess_private_mcp_transport_evaluation(report)
print(readiness.to_dict())
```

`decision_ready=true` means the retained technical evidence is sufficient to choose a result.
`definition_of_done=true` additionally requires `decision_eligible=true` and exactly one recorded final
recommendation.

## 20. Final decision

Choose exactly one only after decision-ready evidence exists:

- `adopt_reference_private_mcp_profile`
- `experimental_only`
- `prefer_simpler_private_networking`
- `reject/defer`

Then update `docs/upstream/OPENZITI_MCP_GATEWAY_EVALUATION.md` and
`upstream/openziti-mcp-gateway.yaml` consistently. Promote the upstream catalog status only if the
chosen lifecycle requires it. #967 remains open while live evidence or the single final recommendation
is missing.
