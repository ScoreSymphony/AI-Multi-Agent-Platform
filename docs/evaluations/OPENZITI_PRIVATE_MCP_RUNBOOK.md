# OpenZiti MCP Gateway two-node evidence runbook

This runbook produces the live evidence required by issue #967. A successful remote tool call is not
sufficient: the campaign must prove canonical authorization separation, non-public backend exposure,
credential handling, revocation/failure behavior, idempotency, lateral-movement limits, self-hosted
cost compatibility and repeatable latency.

## 1. Pinned campaign inputs

Use these source inputs unless a later #967 PR updates the pin and repeats the review:

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

Record the exact self-hosted OpenZiti controller/router images or packages used by the campaign. Do
not use moving `latest` tags in decision evidence.

## 2. Known source-review risk: positional zrok share token

At the pinned revision, zrok-mode `mcp-tools run` and `mcp-tools http` resolve their target from a
**positional share-token argument**. The command accepts Agora as an alternative target, but the zrok
path does not expose an environment-variable, file-descriptor, stdin or secret-file input for the
share token.

This is a material #967 security question, not something the runbook may hide with shell syntax. A
`SecretReference` can protect the value at rest and during canonical serialization, but once resolved
for this CLI the value may still be visible in process argv (for example through `/proc/<pid>/cmdline`
or equivalent host tooling).

Therefore:

1. run the argv exposure check explicitly under a dedicated evaluation account;
2. redact the token from retained evidence while recording whether its exact value was observable;
3. set `secret_handling.process_argv_secret_exposure_detected=true` if it is observable;
4. do **not** mark that profile decision-ready for production adoption while the exposure remains;
5. if a wrapper, library integration, patched upstream binary or Agora path is evaluated as a
   mitigation, record it as a distinct profile with its exact source/revision and operational cost.

Do not claim that exporting the token to an environment variable fixes the problem: the upstream zrok
CLI still receives the resolved value as argv.

## 3. Evidence directory

Create one immutable directory per measured run:

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

The report must validate against
`src/ai_multi_agent_platform/benchmarking/schemas/private-mcp-transport-evaluation-report.v1.schema.json`.
Never retain raw share/enrollment tokens, private identities, session tokens or resolved secret
values. Evidence may retain hashes/fingerprints only when sufficient.

## 4. Required topology

Use two distinct hosts/VPS instances in the same region where practical.

### Node A — platform/client side

- platform Control Plane/Worker or canonical invocation harness;
- #15 authorization/approval active;
- #34 secret handling active;
- pinned `mcp-tools` or the explicitly recorded mitigation profile;
- local MCP proxy bound to loopback only, e.g. `127.0.0.1:18080`;
- no direct public route to an MCP backend port on Node B.

### Node B — gateway/backend side

- pinned `mcp-gateway`;
- bundled `mcp-filesystem` as deterministic backend;
- backend spawned as local stdio child process;
- bounded workspace `/var/lib/ai-multi-agent-platform/issue967/workspace`;
- no MCP backend TCP listener;
- only overlay infrastructure reachability required by the exact self-hosted deployment.

Use `deploy/distributed/openziti-private-mcp-evaluation/gateway.example.yml` as the baseline Node B
configuration.

## 5. Build the pinned MCP Gateway binaries

After retrieving the upstream repository through the normal operator-controlled process:

```bash
git checkout --detach 8f99623d95d2f5223d2fa12b9f125688d8c80bf9
test "$(git rev-parse HEAD)" = "8f99623d95d2f5223d2fa12b9f125688d8c80bf9"
mkdir -p ./bin
go build -o ./bin/mcp-gateway ./cmd/mcp-gateway
go build -o ./bin/mcp-tools ./cmd/mcp-tools
go build -o ./bin/mcp-filesystem ./cmd/mcp-filesystem
sha256sum ./bin/mcp-gateway ./bin/mcp-tools ./bin/mcp-filesystem
```

Retain commit, Go version and binary hashes. A hash from another host is not proof of byte-for-byte
reproducibility unless that property is separately established.

## 6. Prepare the self-hosted overlay

Decision-eligible evidence requires a self-hosted or otherwise already-covered zrok/OpenZiti path.
Record:

- zrok version/revision;
- OpenZiti controller version/image digest;
- OpenZiti router version/image digest;
- database/message-bus components actually required;
- public/private controller/router/frontend ports;
- DNS/TLS endpoints;
- state volumes;
- enrollment/bootstrap procedure;
- restart/backup implications.

Overlay/control-plane listeners must be listed separately from MCP backend exposure. The Node B MCP
backend remains a stdio process with no network listener.

Hosted zrok may be used as separate compatibility evidence, but a hosted-only report must set
`topology.self_hosted_overlay=false` and cannot become decision-ready.

## 7. Enroll Node A and Node B

Treat enable/enrollment tokens and generated private identity material as #34-owned secrets. For the
baseline owner-only share, Node A and Node B may use separately enrolled environments under the same
authorized zrok account. For cross-client isolation, enroll a second account/identity with no service
entitlement and prove denial.

If explicit access grants are used, record the exact ACL mechanism. At the pinned revision,
`zrok.share.access_grants` applies to newly created shares and must not be assumed to combine with a
pre-existing `share_token`.

## 8. Start Node B

```bash
sudo install -d -m 0700 /var/lib/ai-multi-agent-platform/issue967/workspace
sudo install -d -m 0755 /opt/ai-multi-agent-platform/issue967/bin
sudo install -m 0755 ./bin/mcp-filesystem \
  /opt/ai-multi-agent-platform/issue967/bin/mcp-filesystem

./bin/mcp-gateway run \
  /path/to/AI-Multi-Agent-Platform/deploy/distributed/openziti-private-mcp-evaluation/gateway.example.yml
```

Standalone mode emits a share token. Treat the startup channel as secret material: ingest the token
into the evaluation secret flow and do not retain raw startup output as ordinary evidence.

## 9. Prove the backend is not public

Retain redacted process/listener evidence proving:

1. `mcp-filesystem` exists only as a child stdio process;
2. no backend TCP listener binds public or private network interfaces;
3. zrok/OpenZiti listeners are labelled separately.

From a non-enrolled external host, test only the explicitly authorized campaign target/port set and
retain evidence that no MCP backend endpoint is reachable. Do not broaden this into an uncontrolled
scan.

## 10. Start Node A and measure argv exposure

For the unmodified pinned zrok profile, the upstream invocation shape is:

```bash
./bin/mcp-tools http '<resolved-share-token>' --bind 127.0.0.1:18080
```

This command is shown to document upstream behavior, **not** as a claim that the credential handling
is acceptable. Run it only in the dedicated evaluation environment. While it is alive, inspect its
argv from another permitted local process/account boundary and record a redacted yes/no result. If
the resolved token is observable, the report must set:

```json
{
  "process_argv_secret_exposure_detected": true
}
```

and the baseline zrok CLI profile is blocked from production adoption by the readiness evaluator.

Regardless of the argv result, verify the local HTTP listener is loopback-only. The existing platform
adapter target is otherwise ordinary:

```python
MCPServerConfig(
    server_id="issue-967-private",
    endpoint="http://127.0.0.1:18080",
)
```

The share token must never enter `MCPServerConfig`, Task/Run/Capability state or ordinary telemetry.

## 11. Happy-path tools

Exercise through the normal canonical capability path:

- read-only: `fixture:list_directory` or `fixture:read_file`;
- bounded side effect: `fixture:write_file` in the issue-967 workspace.

The write test uses a deterministic idempotency key/canonical invocation identity and a unique path so
duplicate side effects can be detected after reconnect/retry. Direct gateway/provider calls are only
transport diagnostics and must not be used as canonical authorization evidence.

## 12. Identity versus canonical authorization matrix

| Case ID | Procedure | Required outcome |
| --- | --- | --- |
| `authorized_valid_transport` | authorized platform principal + valid overlay identity | invocation succeeds |
| `unauthorized_valid_transport_denied` | deny at #15 while transport remains valid | platform denies before provider execution |
| `invalid_or_revoked_transport_fails_closed` | revoke/disable identity/share access | transport cannot invoke backend |
| `cross_client_service_isolation` | unrelated enrolled identity attempts service | access denied |
| `provider_identifier_not_authority` | know provider/share identifier without entitlement | access still denied |

Capture platform authorization evidence separately from overlay connection evidence.

## 13. Capability/filtering matrix

| Case ID | Required proof |
| --- | --- |
| `canonical_allowlist_authoritative` | agent-visible/invocable tools remain bounded by canonical #12 policy |
| `gateway_filter_cannot_widen` | loosening gateway filters cannot make a #12/#15-denied tool invocable |
| `tool_collision_deterministic` | duplicate original tool names remain deterministically namespaced/mapped |
| `dynamic_backend_no_automatic_grant` | adding backend/tool does not create an authorized capability automatically |

Gateway filtering/path policy remains defense in depth only.

## 14. Secret rotation and revocation

Exercise and retain redacted evidence for:

- share/service credential rotation where the chosen profile supports it;
- overlay identity/environment revocation;
- revocation while an already-established transport/session exists, including an invocation attempt
  after revocation has become effective so stale-session access is measured rather than assumed;
- restart persistence for the chosen persistent-share lifecycle;
- absence of credentials in Connection serialization, canonical state, logs and evidence;
- process-argv exposure of the resolved share token or the exact mitigation that removes it.

If a persistent share is used, document its ACL lifecycle explicitly; do not copy ephemeral
`access_grants` semantics onto a pre-created share.

## 15. Failure/recovery matrix

Run all mandatory cases:

- `gateway_restart`
- `backend_restart`
- `client_restart`
- `network_partition_reconnect`
- `established_session_revocation_fails_closed`: revoke the relevant transport identity/share while
  the transport/session is established; after the provider's documented effective-revocation point,
  prove the stale session can no longer invoke the backend and record any accepted propagation window;
- `service_removal_reconfiguration_fails_closed`: remove or reconfigure the exposed service while a
  client path exists; prove the old path fails closed and capture the recovery behavior after the
  intended service configuration is restored/replaced;
- `node_b_unavailable_before_invocation`
- `node_b_lost_during_invocation`
- `no_duplicate_side_effect_after_retry`

Also retain diagnostics for gateway-up/backend-down, invalid identity, malformed backend response and
overlay control-plane/DNS outage where practical. A new transport session must not silently create a
new canonical Run.

## 16. Lateral-movement boundary

Retain evidence for:

- `unrelated_node_b_services_unreachable`: the evaluated service identity/path cannot reach an
  unrelated test listener on Node B;
- `gateway_not_unrestricted_tunnel`: the supported profile does not expose arbitrary host:port
  proxying.

Provider management APIs remain separate from the agent-facing path. MCP metadata/responses are
untrusted data and must not reconfigure transport or authorization policy.

## 17. Cost and local-baseline cases

Retain:

- `self_hosted_no_new_recurring_paid_service`: useful two-node path uses only already-covered
  infrastructure/software;
- `local_mcp_without_overlay`: existing local MCP operation remains green with the candidate absent.

Failure of the first case prevents baseline adoption.

## 18. Latency measurements

Use the same deterministic read-only operation and comparable payload where possible. Retain at least
five post-warmup observations per distribution:

- `direct_local`;
- `private_overlay_warm`;
- `connection_establishment`;
- `reconnect`.

Keep raw samples, compute p50/p95 from those samples and avoid presenting one VPS campaign as a
universal capacity claim.

## 19. Compare a simpler private-networking path

Run an equivalent workload over the simplest already-available credible private route between the two
nodes (for example an existing SSH/WireGuard/Tailscale-style route when cost-compatible). Compare:

- public backend exposure;
- identity granularity/revocation;
- secret delivery surface, including argv/process-table exposure;
- lateral movement;
- operational state/steps;
- restart/recovery;
- p50/p95 overhead;
- incremental recurring cost.

If the simpler route provides equivalent isolation/recovery at materially lower complexity, that is
evidence for `prefer_simpler_private_networking` rather than a reason to add more overlay machinery.

## 20. Build and validate the report

After evidence files exist and have stable hashes:

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

`decision_ready=true` means technical evidence is sufficient to choose a result.
`definition_of_done=true` additionally requires `decision_eligible=true` and one final recommendation.

## 21. Final decision

Choose exactly one only after decision-ready evidence exists:

- `adopt_reference_private_mcp_profile`
- `experimental_only`
- `prefer_simpler_private_networking`
- `reject/defer`

Then update `docs/upstream/OPENZITI_MCP_GATEWAY_EVALUATION.md` and
`upstream/openziti-mcp-gateway.yaml` consistently. #967 remains open while live evidence or the single
final recommendation is missing.