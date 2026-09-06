# Advanced distributed and heterogeneous deployment

Issue: #240

This document extends the #39 single-server baseline without replacing it. Canonical Node/Worker
identity, scheduling, reservations, Task/Run lifecycle, Workspace references, authentication and
authorization remain owned by the existing platform contracts. Advanced deployment is composition,
not a second orchestration model.

## Architecture boundary

```text
client / CLI / optional frontend
        |
        | authenticated canonical API
        v
Control Plane
        |
        +-- DistributedRuntime / DistributedRegistry / DeterministicScheduler
        |
        +-- Worker protocol HTTP(S): register / heartbeat / deregister
        |
        `-- #35 MessageTransport <----------------------> Worker process
                    |                                      |
                    |                                      +-- WorkerPresenceEndpoint
                    |                                      +-- WorkerTransportEndpoint
                    |                                      +-- WorkerWorkspaceTransportEndpoint
                    |                                      `-- WorkspaceBoundLocalWorker / executor
                    |
                    `-- canonical WorkerJob + Workspace/artifact references
```

Deployment hostnames, ports, filesystem paths, process IDs, service-manager units, VPS products and
hardware model names are deployment metadata only. They never replace `node_*`, `worker_*`,
`workspace_*`, `artifact_*`, `task_*` or `run_*` identities.

The profile loader materializes canonical `NodeRecord`, `WorkerRecord` and `RegistrationRequest`
objects while keeping host-local metadata in deployment bindings.

## Shipped entrypoints

| Command | Purpose |
| --- | --- |
| `platform-server` | unchanged #39 single-node fallback |
| `platform-distributed-server` | Control Plane with canonical distributed runtime, compute administration and Worker protocol |
| `platform-message-broker` | self-hosted TCP implementation of #35 `MessageTransport` |
| `platform-worker` | independent Worker process for one profile binding |
| `platform worker ...` | authenticated northbound Worker inspection/administration and reporter credential lifecycle |

`platform-distributed-server` requires an explicit runnable profile through `--profile` or
`PLATFORM_DISTRIBUTED_PROFILE`. The actual command shape is:

```bash
platform-distributed-server --profile <profile.json> serve
```

Host/port configuration for the Control Plane remains owned by the ordinary #39 deployment
configuration; there are no distributed-only server `--host` or `--port` flags.

## Reference profiles

Credential-free examples are shipped under `deploy/distributed/profiles/`:

| Profile | Purpose |
| --- | --- |
| `multi-local-workers.json` | one Node with multiple independent local Worker processes |
| `remote-worker.json` | Control Plane plus one authenticated remote Worker |
| `cpu-control-gpu-worker.json` | CPU/general Node plus generic accelerator-capable Worker |
| `heterogeneous-three-node.json` | three differently described Nodes using resources/capabilities/locality |

The examples contain no real credential, personal device name, cloud instance ID, VPS SKU or
GPU-vendor-specific canonical identity. Placement uses canonical CPU/RAM/storage, accelerator,
runtime, model, capability, trust, label and locality facts.

## Capability-based placement

Roles emerge from facts rather than host names:

- CPU/RAM/storage minima reject insufficient candidates;
- `gpu="required"` and VRAM requirements select any compatible accelerator rather than a vendor;
- runtime, model and capability requirements constrain candidates;
- locality can prefer Workspace/data/model proximity;
- trust, network availability, drain, maintenance and concurrency remain scheduler gates;
- equal compatible candidates retain #14's deterministic Worker-ID tie-break.

Adding, removing or replacing a Worker changes deployment/registry state, not Task/Run logic.

## Registration, reporters and Worker presence

Each profile Node declares one `reporter_worker_id`. The reporter owns authenticated Node
registration and the complete Node heartbeat snapshot. Sibling Worker processes do not create
competing Node reporters, but **every Worker process owns its own #35 presence, execution and
Workspace endpoints**.

The Worker process starts those endpoints before a reporter attempts registration. On the Control
Plane, the shipped distributed composition probes every reported Worker through the existing #35
transport before accepting its liveness projection. An unreachable Worker is represented with the
same canonical Worker identity but `OFFLINE` status. Consequently, a live reporter cannot keep a
dead sibling schedulable merely by replaying a static profile snapshot. When the same Worker
process/identity becomes reachable again, the next authenticated heartbeat can restore it to
`HEALTHY`.

Presence is deployment reachability evidence only. It does not introduce a second registry,
scheduler or Worker identity model.

The reporter lifecycle is:

1. start local presence/execution/Workspace endpoints;
2. authenticate using the existing #36 Worker credential;
3. bind identity to `RegistrationRequest.service_identity_ref`;
4. pass the credential's canonical scope and #15 authorization;
5. register the canonical Node/Worker snapshot;
6. send monotonic authenticated heartbeats;
7. re-register the same canonical IDs when a restarted Control Plane reports missing state;
8. deregister best-effort during graceful reporter shutdown.

Remote registration cannot self-grant Control-Plane-owned trust, drain or maintenance state.

A registered Worker is attached to the existing runtime through the existing adapters:

```text
TransportWorkerDispatcher
        |
        v
MaterializingWorkerDispatcher
        |
        +-- TransportRemoteWorkspaceMaterializer
        `-- WorkspaceJobMaterializationResolver
        |
        v
DistributedRuntime
```

## Canonical operator administration

The shipped distributed server registers the existing #14 northbound collections and commands:

- `nodes`, `workers`, `worker-jobs`;
- `node.drain`, `node.undrain`;
- `node.maintenance-enable`, `node.maintenance-disable`;
- `worker.drain`, `worker.undrain`.

The ordinary authenticated `platform node ...` and `platform worker ...` CLI therefore inspects and
administers the same canonical runtime used for scheduling. There is no deployment-private admin
shortcut.

## Reporter credential provisioning and rotation

Committed profiles contain only credential references/metadata, never bearer values. The shipped
distributed Control Plane adds two profile-bound operator commands:

```bash
platform --yes worker provision <reporter_worker_id>
platform --yes worker rotate-credential <reporter_worker_id> \
  --credential-id <credential_id>
```

Authenticate the normal CLI first with `platform auth login ...`, `platform auth token activate
--token-stdin`, or a process-local `AI_PLATFORM_TOKEN`. CLI secret state stays outside ordinary
non-secret profile configuration.

Provisioning is restricted to the selected profile's reporter Worker. It:

- creates the minimal persisted #15 Worker policy when none exists;
- issues a canonical #36 Worker credential;
- constrains that credential to `CREATE`, `MODIFY` and `DELETE` for Node/Worker resources;
- further constrains the credential to exactly the profile Node ID and Worker IDs represented by
  that reporter.

The bearer secret is returned once. A repeated provision call does **not** mint another active
credential and does not recover the old secret; it returns safe active credential IDs instead. If
the one-time secret was lost, rotate the named credential and inject the replacement secret into
the reporter process. Rotation revokes the old credential.

Never commit Worker tokens, transport HMAC keys or private keys to profile JSON, source control,
compose files or service-unit arguments.

## Network transport and security

The repository ships `TcpMessageBroker` and `TcpMessageTransport` as a replaceable network-capable
#35 implementation. `InProcessMessageTransport` remains valid for same-process composition/tests.

Security defaults fail closed:

- loopback TCP may be unencrypted for local development;
- non-loopback broker listeners require TLS and either HMAC authentication or mTLS client identity;
- non-loopback `TcpMessageTransport` clients require TLS;
- non-loopback Worker-protocol clients require HTTPS;
- Worker protocol requests use scoped #36 credentials plus nonce/replay protection and #15 checks;
- secrets are supplied at runtime and do not become canonical profile identity.

| Flow | Default scope | Protection |
| --- | --- | --- |
| client/frontend/CLI -> Control Plane | public only when deliberately exposed | normal Control Plane authentication; TLS at public/private edge |
| Worker -> Worker protocol | loopback/private | HTTPS for non-loopback + scoped Worker credential |
| Control Plane <-> Worker messages | loopback/private | TLS plus HMAC or mTLS for non-loopback |
| Workspace/result transfer | same #35 private transport | transport identity + canonical access context |
| optional model/tool/browser/connector | private | service-specific scoped identity where enabled |
| SQLite/filesystem stores | no listener | filesystem/process permissions |

The secure #240 acceptance path generates a temporary CA/server/client certificate set at test
runtime, requires client certificates, performs Worker registration over HTTPS/mTLS and dispatches
canonical work through an mTLS TCP broker. No private test key is committed.

## Workspace, snapshots and artifacts

Only canonical references cross the Worker job boundary: `workspace_ref`, `snapshot_ref`,
`artifact_refs` and secret references. A Control-Plane filesystem path never becomes remote
Workspace identity.

`TransportRemoteWorkspaceMaterializer` streams the exact canonical Workspace snapshot through #35.
The Worker validates paths/checksums, executes in its machine-local root, and returns a result
manifest/changed files. Changed files are reconstructed through the canonical `FileProvider` and
existing artifact references remain references rather than creating a second artifact model.
Zero-byte input and result files use the same prepare/chunk/commit/result contracts and have an
explicit regression test.

## Same-host multi-process example

The checked-in `multi-local-workers.json` reporter is
`worker_00000000-0000-4000-8000-000000000241`; sibling
`worker_00000000-0000-4000-8000-000000000242` is an independent process.

```bash
# 1. broker
platform-message-broker --host 127.0.0.1 --port 8765

# 2. distributed Control Plane
export PLATFORM_MESSAGE_BROKER_HOST=127.0.0.1
export PLATFORM_MESSAGE_BROKER_PORT=8765
platform-distributed-server \
  --profile deploy/distributed/profiles/multi-local-workers.json \
  serve

# 3. from an authenticated operator CLI, issue reporter credential once
platform --yes worker provision worker_00000000-0000-4000-8000-000000000241

# 4. inject the returned one-time secret outside source control
export PLATFORM_WORKER_TOKEN='<runtime-only-worker-credential>'
platform-worker \
  --profile deploy/distributed/profiles/multi-local-workers.json \
  --host-ref device-a \
  --worker-id worker_00000000-0000-4000-8000-000000000241 \
  --control-plane-url http://127.0.0.1:8000 \
  --broker-host 127.0.0.1 \
  --broker-port 8765

# 5. sibling: no Worker-protocol credential because it is not the Node reporter
platform-worker \
  --profile deploy/distributed/profiles/multi-local-workers.json \
  --host-ref device-a \
  --worker-id worker_00000000-0000-4000-8000-000000000242 \
  --control-plane-url http://127.0.0.1:8000 \
  --broker-host 127.0.0.1 \
  --broker-port 8765
```

The sibling still owns its own presence/execution/Workspace endpoints. Its liveness is verified by
the Control Plane through #35 and is not inferred from the reporter process alone.

## CPU + accelerator and heterogeneous examples

For `cpu-control-gpu-worker.json`:

| Binding | Reporter | Meaning |
| --- | --- | --- |
| `device-cpu` | `worker_00000000-0000-4000-8000-000000000262` | general CPU execution |
| `device-accelerated` | `worker_00000000-0000-4000-8000-000000000263` | generic accelerator-capable execution |

For `heterogeneous-three-node.json`:

| Binding | Reporter | Declared facts |
| --- | --- | --- |
| `device-a` | `worker_00000000-0000-4000-8000-000000000273` | Linux/x86_64 general execution + Workspace locality |
| `device-b` | `worker_00000000-0000-4000-8000-000000000274` | Windows/x86_64 accelerator/model capability |
| `device-c` | `worker_00000000-0000-4000-8000-000000000275` | Linux/aarch64 data-local execution |

These binding labels are deployment metadata only. Scheduling uses the canonical facts carried by
#14 contracts.

## Reproducible two-machine flow

A physical two-host installation uses the same composition:

### Machine A — Control Plane / broker

1. choose private reachable DNS/IP endpoints;
2. provision broker TLS and the HTTPS edge used by the Worker-protocol route;
3. configure either high-entropy #35 HMAC authentication or mTLS;
4. start `platform-message-broker` on the private interface;
5. configure `PLATFORM_MESSAGE_BROKER_HOST`, `PLATFORM_MESSAGE_BROKER_PORT`, TLS client values and
   optional `PLATFORM_TRANSPORT_AUTH_KEY` for the Control Plane;
6. start `platform-distributed-server --profile <selected-profile> serve`;
7. authenticate an operator CLI and provision each profile reporter credential;
8. transfer only each one-time secret and required CA/client identity to the corresponding Worker
   host through the operator's runtime secret mechanism.

### Machine B — Worker

1. install the same platform version and copy the credential-free profile;
2. inject `PLATFORM_WORKER_TOKEN` outside the repository/Workspace;
3. configure CA/client certificates and/or transport HMAC secret outside the repository;
4. start `platform-worker` with the HTTPS Control Plane URL and private broker endpoint;
5. verify canonical registration and health from Machine A;
6. submit work whose requirements select that Worker and verify the terminal canonical result.

Automated CI uses real independent OS processes and real TLS/TCP boundaries on one CI host. It does
not claim to simulate two physical machines; the procedure above is the reproducible physical
cross-host operator path.

## Failure and recovery

### Sibling Worker/process loss

The next reporter heartbeat is bounded by per-Worker #35 presence. An unreachable sibling becomes
`OFFLINE` and is excluded from new scheduling even while the reporter remains healthy. The same
Worker ID may become `HEALTHY` again when its endpoint is reachable and the next authenticated
heartbeat reports it.

### Reporter/network interruption

Heartbeat expiry marks stale Node/Worker state offline and new placement excludes it. A network
partition is not proof that already accepted remote execution ended; existing ownership is
reconciled through #14 rather than blindly reassigned.

### Worker restart

Retain canonical IDs, reload the runtime credential, reconnect to #35 and re-register when needed.
Do not create a new Worker ID merely because the OS process restarted.

### Graceful maintenance

Use canonical `worker.drain` or Node drain/maintenance first, allow the intended in-flight state to
settle, then stop the process. Best-effort deregistration is followed by heartbeat/presence expiry
when the Control Plane is unavailable.

## Operator lifecycle

### Add a Worker/Node

1. declare canonical IDs and capability/resource metadata in the selected profile;
2. start/update the profile-aware Control Plane;
3. provision the declared reporter with `platform --yes worker provision <reporter_id>`;
4. inject the one-time credential and transport identity outside source control;
5. start the Worker process(es);
6. verify with `platform node list`, `platform worker list` and the relevant `show` commands.

### Drain/remove/replace

Use canonical drain before planned removal. Permanent topology removal uses explicit Worker
lifecycle/deregistration. Replacement hardware can retain the same logical Worker identity when
policy permits; machine-local paths/hardware metadata can change without changing Task/Run logic.

### Rotate a Worker credential

```bash
platform --yes worker rotate-credential <reporter_id> \
  --credential-id <old_credential_id>
```

Inject the returned replacement token into the reporter process and restart/reload it as required.
The old credential is revoked by the rotation operation.

### Move an optional service

Change only deployment endpoint/adapter configuration. Optional model/tool/browser/connector
addresses do not become canonical Node/Worker/model/tool identity.

### Fall back to #39

Drain/stop distributed Workers and optional distributed services, then run `platform-server`.
Advanced components are not required by the #39 baseline, and canonical Task/Run/Workspace logic
does not migrate into a second architecture.

## Optional services and no-paid-service path

Model, browser, tool and connector services remain optional. A disabled service exposes no endpoint,
and its absence does not invalidate unrelated execution. Enabled services remain private and
replaceable. The reference distributed acceptance path uses local/reference execution and requires
no paid AI/API service.

## Validation coverage

The combined #14/#35/#37/#240 suite covers:

- credential-free/profile validation and provider-neutral heterogeneous examples;
- multiple local Worker registration and dispatch;
- authenticated remote registration, heartbeat and re-registration;
- CPU-only rejection and generic accelerator placement;
- deterministic heterogeneous selection;
- drain/maintenance exclusion;
- network loss, liveness expiry and reconciliation;
- exact Workspace transfer, artifact-reference preservation, result collection and cleanup;
- zero-byte Workspace input/result files;
- optional-service absence/degraded behavior;
- unchanged #39 single-node regression;
- two independent Worker OS processes over real TCP;
- reporter/sibling presence loss and recovery without false `HEALTHY` state;
- profile-bound one-time Worker credential provisioning and rotation;
- shipped distributed server registration of canonical compute/admin resources;
- successful verified mTLS broker transport using runtime-generated certificates;
- secure end-to-end acceptance through the shipped Worker entrypoint: profile-bound provisioning,
  HTTPS/mTLS registration, separate Worker OS process, mTLS TCP dispatch, and normal canonical
  Task/Run completion.

The advanced deployment therefore extends #39 through canonical capability-based Worker contracts
and replaceable secure networking while leaving the ordinary single-node installation valid and
independent.
