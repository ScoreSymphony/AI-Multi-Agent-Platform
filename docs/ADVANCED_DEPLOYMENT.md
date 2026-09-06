# Advanced distributed and heterogeneous deployment

Issue: #240

This document extends the single-server deployment baseline from #39 without changing canonical
platform ownership. Node/Worker identity, registration, heartbeat, scheduling, reservations,
dispatch, reconciliation, Workspace/artifact references, authentication and authorization remain
owned by the existing #14, #35, #36 and #37 contracts.

## Architecture boundary

Advanced deployment is composition, not a second orchestration or lifecycle system:

```text
client / optional frontend
        |
        | canonical northbound API
        v
Authenticated Control Plane
        |
        +-- DistributedRuntime / DistributedRegistry
        |       |
        |       `-- DeterministicScheduler
        |
        +-- Worker protocol HTTP (registration / heartbeat / deregistration)
        |
        `-- #35 TcpMessageTransport <------> Worker process
                    |                           |
                    |                           +-- WorkerTransportEndpoint
                    |                           +-- WorkerWorkspaceTransportEndpoint
                    |                           +-- WorkerWorkspaceMaterializationStore
                    |                           `-- WorkspaceBoundLocalWorker / executor
                    |
                    `-- canonical WorkerJobRequest + Workspace references
```

Deployment hostnames, filesystem paths, ports, process IDs, container IDs and service-manager
units remain deployment metadata. They never replace `node_*`, `worker_*`, `workspace_*`,
`workspace_snapshot_*`, `artifact_*`, `task_*`, `run_*` or other canonical identities.

The strict profile loader in `ai_multi_agent_platform.deployment.advanced_profiles` materializes
canonical `NodeRecord`, `WorkerRecord` and `RegistrationRequest` objects while keeping
machine-local bindings in `WorkerHostBinding`.

## Shipped entrypoints

The package exposes four independent deployment modes/commands:

| Command | Purpose |
| --- | --- |
| `platform-server` | unchanged #39 single-node fallback |
| `platform-distributed-server` | Control Plane with canonical distributed runtime and Worker-protocol surface |
| `platform-message-broker` | self-hosted TCP implementation of the existing #35 `MessageTransport` contract |
| `platform-worker` | independently running Worker process from one #240 profile binding |

The distributed commands are deployment adapters. They do not replace any canonical interface.
`platform-distributed-server` always requires an explicit runnable profile through `--profile` or
`PLATFORM_DISTRIBUTED_PROFILE`; a description-only or reporter-less profile is rejected at startup.
After the distributed-only `--profile` option is consumed, the command uses the ordinary #39 server
subcommands, so a Control Plane is started with `serve`. Listener configuration remains owned by
the normal single-node configuration rather than by distributed-only `--host`/`--port` flags.

## Reference profiles

Credential-free examples are under `deploy/distributed/profiles/`:

| Profile | Purpose |
| --- | --- |
| `multi-local-workers.json` | one Control Plane host with two schedulable local Workers |
| `remote-worker.json` | one Control Plane plus one authenticated remote Worker |
| `cpu-control-gpu-worker.json` | CPU/general host plus a generic accelerator-capable remote Worker |
| `heterogeneous-three-node.json` | general, accelerator/model-local and data-local Nodes |

The examples deliberately avoid VPS plans, GPU vendors, cloud products and fixed machine roles.
Placement follows canonical resources, capabilities, runtimes, models, labels, trust and locality.

## Capability-based role composition

Roles emerge from Node/Worker facts rather than host names. Examples:

- a CPU-only job can run on any healthy Worker satisfying CPU/RAM/storage, executor, runtime,
  capability and policy requirements;
- `gpu="required"` and a VRAM floor reject CPU-only Nodes and select any matching accelerator;
- `model_ref` constrains placement to Workers reporting that model;
- `capability_refs` can represent browser, tool, model-serving or application-specific abilities;
- `locality_refs` can prefer data/Workspace/model locality without creating hard-coded roles;
- drain, maintenance, trust, heartbeat health and concurrency remain scheduler gates.

Equal candidates retain #14's deterministic Worker-ID tie-break.

## Registration, reporters and discovery

A profile Node may declare several Worker processes. Exactly one `reporter_worker_id` owns the
complete authenticated Node registration and heartbeat snapshot. Sibling Worker processes expose
their own execution/Workspace endpoints but do not create competing Node reporters.

The reporter:

1. authenticates with the existing #36 Worker credential;
2. binds authenticated identity to `RegistrationRequest.service_identity_ref`;
3. passes credential scope plus #15 authorization;
4. registers the complete canonical Node/Worker snapshot;
5. sends monotonic authenticated heartbeats;
6. re-registers with the same canonical IDs when the Control Plane reports missing registration;
7. deregisters its Worker best-effort during graceful shutdown.

Remote registration cannot self-grant Control-Plane-owned trust, drain or maintenance state.
Registration/liveness/state-change timestamps remain Control-Plane-owned and are not accepted as
Worker-authored wire state.

A successful authenticated registration is composed into the existing runtime as:

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
DistributedRuntime.attach_worker(...)
```

Re-registration on the same running Control Plane does not replace an already attached dispatcher,
which prevents heartbeat/reconnect traffic from discarding in-flight materialization/result state.
After explicit graceful deregistration the deployment binding is released so the same canonical
Worker ID can be attached again on a later re-registration.

## Network transport

The repository ships a real network-capable #35 adapter:

- `TcpMessageBroker` — self-hosted broker/server;
- `TcpMessageTransport` — Control Plane and Worker client adapter.

The existing `InProcessMessageTransport` remains useful for same-process tests and local
composition. It is no longer the only reference transport.

Security defaults fail closed:

- loopback TCP may run without TLS for local development/tests;
- non-loopback broker listeners require TLS plus HMAC authentication or mTLS identity;
- non-loopback `TcpMessageTransport` clients require TLS;
- the private Worker-protocol HTTP client requires HTTPS for non-loopback Control Plane URLs;
- bearer credentials and transport HMAC keys are supplied at runtime, not committed in profiles.

Positive TLS/mTLS acceptance is covered by the #240 hardening suite using a runtime-generated test
CA, server certificate and client certificate. No private key is committed to the repository.

The profile field `transport_endpoint_ref` remains deployment metadata; it does not become a
canonical Worker/Node/transport identity.

## Workspace, snapshots and artifacts

Only canonical references cross the job boundary:

- `workspace_ref`;
- `snapshot_ref`;
- input/output `artifact_refs`;
- secret references.

A Control-Plane filesystem path is never transmitted as Worker Workspace identity.
`TransportRemoteWorkspaceMaterializer` reads the exact canonical Workspace snapshot and streams
its files through #35. `WorkerWorkspaceMaterializationStore` validates paths/checksums and builds a
machine-local execution tree under the Worker's configured absolute root. The actual execution
path is adapter-private.

For an execution requiring materialization, the sequence is:

```text
canonical Workspace + snapshot
        |
        v
TransportRemoteWorkspaceMaterializer
        |
        | prepare / chunks / commit
        v
WorkerWorkspaceMaterializationStore
        |
        v
WorkspaceBoundLocalWorker
        |
        | execute in Worker-local tree
        v
result manifest / changed files
        |
        v
canonical FileProvider + preserved artifact refs
        |
        v
cleanup / retain according to outcome
```

Changed output files are reconstructed as canonical `FileRecord` state. Canonical artifact
references already attached to the Worker job/result remain references and survive the remote
transport/result path; this layer does not invent a second artifact-registration model. Zero-byte
files use the same prepare/chunk/commit and result-manifest/result-chunk contracts as non-empty
files; the final #240 regression suite explicitly covers empty input and empty result files.

## Network and exposure matrix

Ports are operator choices. The profiles therefore use logical endpoint references rather than
provider-specific fixed ports.

| Flow | Default scope | Required protection | Notes |
| --- | --- | --- | --- |
| browser/client -> Control Plane | public only when explicitly enabled | normal Control Plane auth; TLS for public exposure | canonical northbound API |
| Control Plane -> local Worker | loopback/private | authenticated #35 transport; loopback TLS optional | no public Worker listener |
| Control Plane <-> remote Worker messages | private | TLS plus HMAC or mTLS | `TcpMessageTransport` |
| remote Worker -> registration/heartbeat | private | HTTPS, scoped #36 Worker credential, nonce/replay protection | Worker-protocol ASGI route |
| Workspace/artifact transfer | same private #35 transport | transport identity + canonical access context | no Control-Plane paths |
| Worker -> local model endpoint | loopback/private | deployment-specific auth if needed | optional and replaceable |
| browser/tool/connector service | private by default | scoped service identity | optional |
| SQLite/local filesystem stores | no listener | filesystem permissions | never direct network services |

## Runnable same-host multi-process profile

The checked-in `multi-local-workers.json` profile contains one Node with two independent Worker
processes. The reporter is
`worker_00000000-0000-4000-8000-000000000241`; sibling
`worker_00000000-0000-4000-8000-000000000242` runs the same execution/Workspace endpoints but does
not emit a competing Node heartbeat.

```bash
# terminal 1: broker
platform-message-broker --host 127.0.0.1 --port 8765

# terminal 2: profile-aware distributed Control Plane
export PLATFORM_MESSAGE_BROKER_HOST=127.0.0.1
export PLATFORM_MESSAGE_BROKER_PORT=8765
platform-distributed-server \
  --profile deploy/distributed/profiles/multi-local-workers.json \
  serve

# terminal 3: reporter Worker
export PLATFORM_WORKER_TOKEN='<runtime-only-worker-credential>'
platform-worker \
  --profile deploy/distributed/profiles/multi-local-workers.json \
  --host-ref device-a \
  --worker-id worker_00000000-0000-4000-8000-000000000241 \
  --control-plane-url http://127.0.0.1:8000 \
  --broker-host 127.0.0.1 \
  --broker-port 8765

# terminal 4: sibling Worker; no Node reporter ownership
platform-worker \
  --profile deploy/distributed/profiles/multi-local-workers.json \
  --host-ref device-a \
  --worker-id worker_00000000-0000-4000-8000-000000000242 \
  --control-plane-url http://127.0.0.1:8000 \
  --broker-host 127.0.0.1 \
  --broker-port 8765
```

The token shown is intentionally a placeholder. The actual scoped reporter credential must be
provisioned through #36 and injected at runtime. The sibling process still uses the same canonical
Worker protocol/runtime composition; `connection_mode="local"` is locality metadata, not a second
unauthenticated Worker model.

## CPU + accelerator and heterogeneous profile startup

Every Node in the runnable reference profiles has exactly one reporter. Start the distributed
server once with the selected profile, then start one `platform-worker` process per declared
Worker, using that Worker's `host_ref` and `worker_id`.

For `cpu-control-gpu-worker.json`:

| Host binding | Reporter Worker | Placement meaning |
| --- | --- | --- |
| `device-cpu` | `worker_00000000-0000-4000-8000-000000000262` | general CPU execution |
| `device-accelerated` | `worker_00000000-0000-4000-8000-000000000263` | generic accelerator-capable execution |

For `heterogeneous-three-node.json`:

| Host binding | Reporter Worker | Declared facts |
| --- | --- | --- |
| `device-a` | `worker_00000000-0000-4000-8000-000000000273` | Linux/x86_64 general execution, primary Workspace locality |
| `device-b` | `worker_00000000-0000-4000-8000-000000000274` | Windows/x86_64 accelerator/model-serving capability |
| `device-c` | `worker_00000000-0000-4000-8000-000000000275` | Linux/aarch64 data-local execution |

Those host labels are deployment bindings only. Scheduling still uses the canonical capability,
resource, runtime, model, OS and locality metadata carried by #14 contracts.

A profile-aware Control Plane launch follows the same shape for either profile:

```bash
export PLATFORM_MESSAGE_BROKER_HOST=127.0.0.1
export PLATFORM_MESSAGE_BROKER_PORT=8765
platform-distributed-server \
  --profile deploy/distributed/profiles/heterogeneous-three-node.json \
  serve
```

For non-loopback Workers, replace the loopback endpoints with private reachable endpoints and
configure the TLS/HTTPS settings described below.

## Reproducible two-machine flow

A physical two-host deployment uses the same composition with network security enabled.

### Machine A — Control Plane / broker

1. choose private DNS/IP names reachable by the Worker host;
2. provision a server certificate for the message broker and an HTTPS certificate/edge for the
   Control Plane Worker-protocol route;
3. provision either a shared high-entropy #35 HMAC key or mTLS client identity;
4. start `platform-message-broker` on the private interface with `--cert-file`/`--key-file`
   (and `--client-ca-file` when using mTLS);
5. configure `PLATFORM_MESSAGE_BROKER_HOST`, `PLATFORM_MESSAGE_BROKER_PORT`, CA/client TLS values
   and optionally `PLATFORM_TRANSPORT_AUTH_KEY` for `platform-distributed-server`;
6. start `platform-distributed-server --profile <selected-profile> serve`;
7. expose the Control Plane through an HTTPS reverse proxy/private TLS edge; the standard HTTP
   application remains deployment-neutral and does not own certificate termination.

### Machine B — Worker

1. install the same platform package/version;
2. copy only the credential-free profile/configuration required by that Worker;
3. provision its scoped #36 Worker credential outside the Workspace/repository;
4. provision CA/client TLS files and/or the transport HMAC key outside the Workspace;
5. set `PLATFORM_WORKER_TOKEN` and, when used, `PLATFORM_TRANSPORT_AUTH_KEY` in the process
   environment/secret manager;
6. run `platform-worker` with the HTTPS Control Plane URL and private broker host/port;
7. verify canonical Node/Worker registration, heartbeat and scheduler eligibility from Machine A;
8. submit a job that actually requires that Worker's capabilities and verify the terminal result.

The repository's automated socket/process tests establish real TCP/process boundaries on one CI
host. They do **not** claim that CI itself owns two physical machines. The two-machine procedure
above is the reproducible operator acceptance path for an actual cross-host installation.

## Credential provisioning

Committed profile JSON contains only `SecretReference` locator/scope metadata. It never contains
the credential value. A Worker credential must be created by the existing #36 authentication
service with:

- actor type `worker`;
- the exact canonical `worker_*` owner identity;
- scoped `CREATE`, `MODIFY` and `DELETE` permissions for Node/Worker resources required by the
  private protocol;
- a corresponding #15 Worker policy for the same operations/resources.

The issued bearer secret is one-time runtime material. Store/inject it through the operator's
secret mechanism and expose it only to the corresponding Worker process.

## Failure and recovery

### Missed heartbeat / network interruption

`DistributedRegistry.expire_heartbeats()` marks stale Nodes and Workers offline. New placement
excludes them. Existing accepted jobs are reconciled rather than assumed to have stopped.

A retryable transport outage does not trigger blind re-registration or replacement of an active
Control-Plane dispatcher. A network partition is also not proof that remote execution ended;
cross-Worker failover must satisfy the existing ownership-fencing rules.

### Worker restart

For a normal Worker restart:

1. retain the same canonical Node/Worker IDs;
2. load the scoped credential from runtime secret storage;
3. reconnect to the configured #35 transport;
4. register/re-register the canonical snapshot when needed;
5. resume heartbeats using fresh nonces/request IDs;
6. let canonical reconciliation resolve previously owned Worker Jobs.

Do not mint a new Worker ID merely because an OS process restarted.

### Control Plane restart

Persisted health is not treated as fresh liveness evidence. Workers reconnect/re-register and
re-establish reachability. Dispatch ownership/reservation recovery remains a #14 concern.

### Graceful shutdown

Use canonical drain state before planned maintenance. Stop assigning new work, allow the intended
in-flight state to settle, then stop the Worker. The process attempts explicit deregistration; if
the Control Plane is unreachable, heartbeat expiry provides the canonical fallback.

## Operator lifecycle

### Add a Worker

1. choose/provision canonical Node and Worker IDs;
2. declare resources/capabilities/runtimes/locality in the profile;
3. provision the scoped Worker credential and transport identity outside source control;
4. start the broker and profile-aware Control Plane if not already running;
5. start the Worker process;
6. verify registration, heartbeat and scheduler eligibility.

### Drain a Worker

Set canonical Worker drain state before maintenance. New jobs are rejected while existing
ownership remains visible for reconciliation. Stop only after reaching the intended in-flight
state.

### Remove or replace a Worker

Use explicit deregistration for permanent removal. A hardware/process replacement may retain the
same canonical Worker identity only when it represents the same logical Worker and security
policy permits it. Replacement hardware may change deployment metadata without changing Task/Run
logic.

### Rotate credentials

1. create/activate the replacement scoped credential;
2. update runtime secret injection/`SecretReference` locator if needed;
3. restart/reload the Worker with the replacement;
4. verify registration and heartbeat;
5. revoke the old credential.

Never put the new token or HMAC key in committed JSON, compose files, unit files or command-line
arguments.

### Move optional services

Change only deployment endpoint/adapter configuration. Model/tool/browser/connector addresses do
not become canonical model/tool/Node/Worker IDs.

### Fall back to #39

Stop/remove remote Workers and optional distributed services, then run `platform-server` again.
Canonical Task/Run/Workspace identity does not require migration into a second store.

## Optional services and no-paid-service path

Profiles may declare optional model, browser, connector or other services. Disabled services have
no endpoint. Enabled services remain replaceable/private and their degradation affects only
independent capabilities.

The checked-in no-paid-service path uses local/reference execution and requires no paid external
service. Advanced distributed deployment itself does not introduce a paid API dependency.

## Security checklist

- scoped/replay-protected Worker credentials;
- TLS for every non-loopback Worker/message connection;
- HMAC or mTLS identity on non-loopback message broker listeners;
- no raw credentials or private keys in profiles/source control;
- remote registration cannot alter administrative trust/drain/maintenance ownership;
- Worker Workspace roots contain only materialized execution data, not application credentials;
- private-by-default optional services;
- no direct browser access to Worker/broker/private backend services;
- telemetry/logging follows existing redaction rules.

## Validation coverage

The #240 suite covers profile parsing/secret rejection, canonical registration and scheduling,
resource/capability placement, drain/maintenance and heartbeat behavior, Worker-protocol HTTP,
Worker process registration/heartbeat/shutdown, transport dispatch, automatic Control-Plane
attachment, two independent Worker OS processes, graceful re-registration, TCP loss with liveness
expiry and same-ID restart, and #39 regression. The #388/#433 suites additionally exercise real
TCP reconnect/redelivery, exact remote Workspace transfer, process isolation, checksums, result
collection and cleanup.

The final #240 hardening coverage adds:

- zero-byte canonical Workspace input materialization;
- zero-byte changed/result file reconstruction through the canonical `FileProvider`;
- a successful verified mTLS broker/client publish/subscribe/ack path with runtime-generated test
  certificates;
- profile-aware operator examples for same-host, CPU+accelerator and heterogeneous deployments.

The composed #240 socket acceptance path exercises scheduler -> network Worker dispatch -> exact
Workspace materialization -> execution -> canonical result/file collection -> cleanup, while the
network-loss acceptance path verifies scheduler exclusion during offline state and execution again
after same-ID re-registration.