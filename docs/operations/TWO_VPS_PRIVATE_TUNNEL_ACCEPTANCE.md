# Real two-VPS private-tunnel distributed acceptance

Issue: #562

> Dependency status: #562 declares #46 as a hard dependency. This runbook and its reusable evidence
> harness may be prepared before #46 closes, but a repository-only run or this document alone does
> **not** complete #562. Final acceptance requires execution on two independent operator-controlled
> VPS hosts after the dependency is resolved (or the issue dependency is explicitly revised).

This runbook validates the already implemented #14/#240 distributed architecture across a real
network boundary. It does not define a new Node/Worker model, scheduler, transport, authentication
system or tunnel-specific canonical identity.

## Architecture under test

```text
Client / operator CLI
        |
        v
Host A: Control Plane + scheduler
        |
        |  private host-to-host tunnel
        |
        +-- Worker protocol HTTPS ----+
        |                              |
        +-- #35 MessageTransport ------+----> Host B: canonical Node/Worker
                                              |
                                              `-- reference executor / Workspace
```

Canonical Task/Plan/Step/Run/Node/Worker/WorkerJob IDs remain platform-owned. Public IPs, VPS
provider IDs, WireGuard peer identities, interface names, ports and host labels are deployment or
evidence metadata only.

## Reference tunnel

The checked-in reference uses WireGuard, but the platform remains tunnel-neutral. The same
acceptance may be run over another private overlay if the network/security invariants are preserved.

Reference values:

- Host A tunnel address: `10.203.0.1/30`;
- Host B tunnel address: `10.203.0.2/30`;
- Host A listens for the WireGuard handshake on UDP `51820`;
- Host B initiates the tunnel and uses `PersistentKeepalive = 25`;
- Worker protocol HTTPS is exposed on the **tunnel/private interface only** (example `8443/tcp`);
- the #35 message broker is exposed on the **tunnel/private interface only** (example `8765/tcp`);
- public client HTTPS, when used, is a separate deliberate edge (typically `443/tcp`).

The private addresses and ports are reference deployment values, not scheduler constants.

Credential-free examples are in `deploy/distributed/two-vps/`.

## Networking matrix

| Flow | Source | Destination | Reference port | Required exposure | Protection |
| --- | --- | --- | ---: | --- | --- |
| WireGuard handshake | Host B / Internet | Host A public edge | `51820/udp` | public only for tunnel establishment | WireGuard peer keys |
| Worker registration/heartbeat | Host B | Host A `10.203.0.1` | `8443/tcp` | tunnel/private only | HTTPS + #36 Worker credential + #15 authorization |
| Worker command/result | Host A + Host B | Host A broker `10.203.0.1` | `8765/tcp` | tunnel/private only | TLS plus HMAC and/or mTLS |
| Workspace/result transfer | Host A + Host B | same #35 path | `8765/tcp` | tunnel/private only | same transport identity + canonical references |
| client/API | operator/client | deliberate Host A TLS edge | deployment-defined | public only when intentionally enabled | normal Control Plane auth + TLS |

Tunnel encryption is **not** a substitute for #36 Worker/service authentication or #15 server-side
authorization.

## Firewall baseline

Translate these rules to the host firewall used by the deployment. Do not blindly copy interface
names from this document.

Host A:

1. allow the selected WireGuard UDP handshake port on the external interface;
2. allow Worker protocol HTTPS only from the tunnel subnet/interface;
3. allow the message broker only from the tunnel subnet/interface;
4. deny the Worker protocol and broker on the external/public interface;
5. expose the ordinary public client/TLS edge only if the deployment intentionally needs it;
6. keep SQLite/filesystem/persistence stores listener-free as in #240.

Host B:

1. allow outbound tunnel establishment to Host A;
2. allow established tunnel traffic;
3. do not expose a public Worker/control-plane service merely for this acceptance;
4. preserve normal host egress policy required by the selected executor and no more.

A valid result proves both private reachability and public non-reachability from an independently
routed perspective. Local firewall configuration text alone is not evidence.

## Service startup order

Use the same immutable platform commit on both hosts.

1. bring up the private tunnel and verify peer reachability;
2. start Host A private TLS/broker dependencies;
3. start `platform-message-broker` on Host A's tunnel/private address;
4. start `platform-distributed-server --profile deploy/distributed/profiles/remote-worker.json serve`;
5. expose the Worker protocol route through the deployment's HTTPS edge on the tunnel address only;
6. provision the profile reporter credential through the authenticated normal CLI;
7. inject Worker and transport credentials into Host B through operator-managed runtime secret
   files/environment;
8. start `platform-worker` on Host B using the same profile and canonical Worker identity;
9. verify registration/heartbeat before submitting work.

On restart, retain canonical Node/Worker identity. Process-instance identity may change and should be
captured only as Evidence.

## Preparation on both hosts

Check out the exact same immutable commit and install the repository package:

```bash
git rev-parse HEAD
python -m pip install -e ".[dev]"
```

Record the commit SHA in sanitized evidence. Do not retain repository credentials or shell history
containing bearer values.

Copy the credential-free profile and the appropriate WireGuard example. Replace placeholders only
in host-local operator configuration outside the repository.

## Phase 1 — tunnel and exposure smoke

Bring up the tunnel using the host's service manager or `wg-quick` as appropriate. Confirm the peers
are established and the two reference private endpoints can exchange ordinary traffic.

Run the repository probe **from Host B** for both required private endpoints. The target address is
used at runtime but is deliberately omitted from the JSON report:

```bash
python scripts/acceptance/two_vps_private_tunnel.py probe \
  --platform-commit "$PLATFORM_COMMIT" \
  --source-host-label host-b \
  --endpoint-label worker-protocol \
  --scope private \
  --target-address "$HOST_A_TUNNEL_ADDRESS" \
  --port 8443 \
  --expect reachable \
  --json-report issue562-worker-protocol-private.json

python scripts/acceptance/two_vps_private_tunnel.py probe \
  --platform-commit "$PLATFORM_COMMIT" \
  --source-host-label host-b \
  --endpoint-label message-broker \
  --scope private \
  --target-address "$HOST_A_TUNNEL_ADDRESS" \
  --port 8765 \
  --expect reachable \
  --json-report issue562-message-broker-private.json
```

From an independently routed path that does **not** traverse the tunnel (Host B's public route is
suitable when routing is unambiguous), probe Host A's public address and require both internal
services to be closed:

```bash
python scripts/acceptance/two_vps_private_tunnel.py probe \
  --platform-commit "$PLATFORM_COMMIT" \
  --source-host-label host-b \
  --endpoint-label worker-protocol \
  --scope public \
  --target-address "$HOST_A_PUBLIC_ADDRESS" \
  --port 8443 \
  --expect closed \
  --json-report issue562-worker-protocol-public.json

python scripts/acceptance/two_vps_private_tunnel.py probe \
  --platform-commit "$PLATFORM_COMMIT" \
  --source-host-label host-b \
  --endpoint-label message-broker \
  --scope public \
  --target-address "$HOST_A_PUBLIC_ADDRESS" \
  --port 8765 \
  --expect closed \
  --json-report issue562-message-broker-public.json
```

Also verify MTU with an ordinary request/response payload larger than a trivial TCP handshake. The
measured RTT/MTU is diagnostic evidence only and must not become a scheduler constant.

## Phase 2 — authenticated registration and heartbeat

The reference profile reporter is:

```text
worker_00000000-0000-4000-8000-000000000251
```

From an authenticated operator CLI on Host A, provision the reporter credential once:

```bash
platform --yes worker provision \
  worker_00000000-0000-4000-8000-000000000251 \
  --secret-file "$HOST_B_WORKER_TOKEN_FILE"
```

Transfer only the one-time secret and required CA/client identity through the operator's runtime
secret mechanism. Never commit them.

Start Host B's Worker using the existing #240 entrypoint and the tunnel/private endpoints:

```bash
export PLATFORM_WORKER_TOKEN="$(cat "$HOST_B_WORKER_TOKEN_FILE")"
platform-worker \
  --profile deploy/distributed/profiles/remote-worker.json \
  --host-ref device-b \
  --worker-id worker_00000000-0000-4000-8000-000000000251 \
  --control-plane-url "https://${HOST_A_TUNNEL_ADDRESS}:8443" \
  --broker-host "$HOST_A_TUNNEL_ADDRESS" \
  --broker-port 8765
```

Configure the TLS/HMAC/mTLS runtime inputs exactly as documented in
`docs/operations/ADVANCED_DEPLOYMENT.md`; do not put secret values on argv or in the profile.

On Host A inspect the canonical state only through the normal Control Plane CLI:

```bash
platform --json node show node_00000000-0000-4000-8000-000000000250
platform --json worker show worker_00000000-0000-4000-8000-000000000251
```

Record a sanitized phase report after confirming authenticated registration, healthy heartbeat and
real resource/capability advertisement:

```bash
python scripts/acceptance/two_vps_private_tunnel.py record-registration \
  --platform-commit "$PLATFORM_COMMIT" \
  --node-id node_00000000-0000-4000-8000-000000000250 \
  --worker-id worker_00000000-0000-4000-8000-000000000251 \
  --authentication-mode worker-credential+https+mtls \
  --authenticated true \
  --heartbeat-healthy true \
  --capability execution:general \
  --capability tool.workspace.write_artifact \
  --cpu-cores "$OBSERVED_CPU_CORES" \
  --ram-bytes "$OBSERVED_RAM_BYTES" \
  --json-report issue562-registration.json
```

Use the values reported by canonical platform state. Do not substitute provider SKU metadata.

## Phase 3 — canonical remote dispatch

Create and start a deterministic no-paid-service Task through the ordinary Control Plane CLI. The
CLI is API-first and uses the same Task/Run lifecycle regardless of local or remote placement:

```bash
platform task create \
  --title "Issue 562 remote reference job" \
  --objective "Execute the deterministic reference path on the eligible remote Worker" \
  --owner-type user \
  --owner-id operator
platform task queue "$TASK_ID"
platform task start "$TASK_ID"
```

Use a deployment/task configuration whose canonical requirements include a capability present on
Host B (for the shipped profile, `execution:general` and/or `tool.workspace.write_artifact`) and do
not use a hostname/provider rule. Inspect the resulting canonical Run and WorkerJob:

```bash
platform --json task show "$TASK_ID"
platform --json run list --task-id "$TASK_ID"
platform --json worker-job list
platform --json worker-job show "$WORKER_JOB_ID"
```

Verify scheduler evidence says Host B was selected through capability/resource policy. Capture the
canonical IDs, correlation ID and trace ID from the platform/telemetry surfaces, then record only
sanitized evidence:

```bash
python scripts/acceptance/two_vps_private_tunnel.py record-dispatch \
  --platform-commit "$PLATFORM_COMMIT" \
  --worker-id "$WORKER_ID" \
  --task-id "$TASK_ID" \
  --run-id "$RUN_ID" \
  --worker-job-id "$WORKER_JOB_ID" \
  --result-status succeeded \
  --correlation-id "$CORRELATION_ID" \
  --returned-correlation-id "$RETURNED_CORRELATION_ID" \
  --trace-id "$TRACE_ID" \
  --returned-trace-id "$RETURNED_TRACE_ID" \
  --duplicate-run-count 0 \
  --selected-by-capability-policy true \
  --json-report issue562-dispatch.json
```

The original Task/Run/WorkerJob records and telemetry remain the authoritative evidence. The phase
JSON is a sanitized acceptance summary, not lifecycle truth.

If the narrower #388 two-host transport harness is run against the same canonical Worker, keep its
sanitized first-run transport report; `finalize` can bind it as additional Artifact/Evidence
round-trip proof.

## Phase 4 — deliberate tunnel interruption

While Host B is registered (and, when practical, while a deterministic job/reservation is active),
interrupt only the tunnel. Do not terminate the Control Plane.

Example on Host B:

```bash
sudo wg-quick down wg0
```

Wait for the configured canonical heartbeat/liveness expiry. Then verify on Host A:

- the remote Node/Worker becomes unavailable/offline according to #14;
- a new task requiring that Worker capability is not placed there;
- an active reservation/job is reconciled according to existing #14 policy;
- no remote `running` state remains trusted indefinitely merely because the Worker disappeared.

Inspect through `platform node/worker/worker-job` and retained telemetry, then record:

```bash
python scripts/acceptance/two_vps_private_tunnel.py record-interruption \
  --platform-commit "$PLATFORM_COMMIT" \
  --worker-id "$WORKER_ID" \
  --heartbeat-expired true \
  --worker-unavailable true \
  --new-placement-blocked true \
  --active-state-reconciled true \
  --indefinite-running-state false \
  --json-report issue562-interruption.json
```

## Phase 5 — tunnel recovery and re-registration

Restore the tunnel:

```bash
sudo wg-quick up wg0
```

Do not edit canonical Node/Worker state manually. Let the Worker reconnect/re-register through the
normal reporter path. Verify:

- the same canonical Worker ID returns;
- heartbeat becomes healthy;
- stale registration/reservation state does not regain authority;
- a new deterministic remote Task succeeds;
- duplicate callbacks/dispatches do not create another canonical Run for the same attempt.

Record:

```bash
python scripts/acceptance/two_vps_private_tunnel.py record-recovery \
  --platform-commit "$PLATFORM_COMMIT" \
  --worker-id "$WORKER_ID" \
  --re-registered true \
  --heartbeat-recovered true \
  --stale-authority-rejected true \
  --duplicate-run-count 0 \
  --post-recovery-run-id "$RECOVERY_RUN_ID" \
  --post-recovery-worker-job-id "$RECOVERY_WORKER_JOB_ID" \
  --post-recovery-status succeeded \
  --json-report issue562-recovery.json
```

## Phase 6 — independent Worker-process restart

Keep the tunnel and Control Plane running. Stop only `platform-worker` on Host B, retain the same
canonical Worker ID/credential policy, and start it again through the normal service command.
Capture non-secret process-instance Evidence references before/after restart if available; they must
be different while the canonical Worker ID stays the same.

Verify re-registration, healthy heartbeat and one later successful deterministic dispatch, then:

```bash
python scripts/acceptance/two_vps_private_tunnel.py record-restart \
  --platform-commit "$PLATFORM_COMMIT" \
  --worker-id "$WORKER_ID" \
  --first-process-evidence-ref "$FIRST_PROCESS_EVIDENCE_REF" \
  --second-process-evidence-ref "$SECOND_PROCESS_EVIDENCE_REF" \
  --re-registered true \
  --heartbeat-recovered true \
  --post-restart-run-id "$POST_RESTART_RUN_ID" \
  --post-restart-status succeeded \
  --json-report issue562-restart.json
```

## Security negative checks

Run the negative authentication checks through the same Worker-protocol boundary:

1. attempt registration/heartbeat without a Worker credential and require rejection;
2. attempt with a revoked/invalid Worker credential and require rejection;
3. confirm an authenticated Worker still cannot bypass #15 server-side authorization;
4. repeat the public-port probes and require both internal endpoints to remain closed;
5. inspect the generated reports/log samples for reusable secrets;
6. confirm provider/tunnel metadata did not become canonical Node/Worker/Task/Run identity.

Record only the outcomes:

```bash
python scripts/acceptance/two_vps_private_tunnel.py record-security \
  --platform-commit "$PLATFORM_COMMIT" \
  --worker-ports-publicly-closed true \
  --unauthenticated-registration-rejected true \
  --invalid-credential-rejected true \
  --authorization-server-side true \
  --reusable-secrets-found false \
  --provider-metadata-canonicalized false \
  --json-report issue562-security.json
```

Do not retain a rejected bearer token or Authorization header as evidence.

## Final sanitized report

After all real-host phases pass, produce the compact report:

```bash
python scripts/acceptance/two_vps_private_tunnel.py finalize \
  --registration issue562-registration.json \
  --dispatch issue562-dispatch.json \
  --interruption issue562-interruption.json \
  --recovery issue562-recovery.json \
  --restart issue562-restart.json \
  --security issue562-security.json \
  --network-report issue562-worker-protocol-private.json \
  --network-report issue562-worker-protocol-public.json \
  --network-report issue562-message-broker-private.json \
  --network-report issue562-message-broker-public.json \
  --json-report issue562-two-vps.json
```

If a compatible #388 transport report from the same canonical Worker exists, add:

```text
--transport-report <sanitized-issue388-transport-report.json>
```

The finalizer fails closed when commits/host labels/Worker identity drift, a required phase failed,
an internal service is publicly reachable, correlation/trace identity is not preserved, duplicate
Runs are observed, interruption/recovery semantics are incomplete, or a report contains obvious
secret-bearing fields.

The final output deliberately retains no target address, public IP, credential value, private key
or provider account identifier.

## #46 conformance relationship

The final report marks the result as optional real-infrastructure evidence for distributed scenario
`E`; it does **not** replace #46's fast deterministic/simulated acceptance tests. A release may
claim this real-two-VPS profile only when the real report exists and passes. Absence of real-VPS
evidence must remain explicit `not-run`/unsupported evidence rather than being inferred from the
simulated two-node suite.

Because #562 currently declares #46 as a hard dependency, integrating the real report into a final
#46 pre-closure audit requires the dependency relationship to be explicitly revised first. Do not
create an implicit cycle by treating this runbook as an exception.

## Evidence retention checklist

Retain only sanitized material sufficient for audit:

- immutable platform commit/release;
- deployment profile (`real-two-vps-private-tunnel`);
- generic Host A/Host B labels;
- tunnel/reference transport type and non-secret version metadata;
- canonical Node/Worker/Task/Run/WorkerJob IDs;
- registration/heartbeat transitions;
- scheduler selection/rejection evidence;
- dispatch/result timing and trace/correlation references;
- interruption window and recovery outcome;
- restart/re-registration outcome;
- phase pass/fail;
- optional sanitized #388 Artifact/Evidence-reference result.

Never retain private keys, bearer/HMAC values, reusable Worker credentials, private/public host
addresses, provider account identifiers or raw sensitive payloads merely for conformance evidence.

## Completion boundary

Repository tests can prove that this harness is deterministic, fail-closed and secret-minimizing,
but they cannot prove real two-VPS behavior. #562 is complete only after the runbook has actually
passed on two independent operator-controlled VPS hosts and the dependency state allows the issue to
close.
