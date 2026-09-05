# Advanced distributed and heterogeneous deployment

Issue: #240

This document extends the single-server deployment baseline from #39 without changing the
canonical runtime architecture. Node/Worker identity, registration, heartbeat, scheduling,
reservations, dispatch, reconciliation, workspace/artifact references and security continue to
come from #14, #35, #36 and #37.

## Architecture boundary

Advanced deployment is composition, not a second orchestration system:

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
        +-- local Worker(s) -----------------> LifecycleBackend / executor
        |
        `-- replaceable #35 transport -------> remote Worker endpoint
                    |                                |
                    |                                +-- local workspace materialization
                    |                                +-- local secret resolution
                    |                                `-- LifecycleBackend / executor
                    |
                    `-- canonical WorkerJobRequest only
```

Deployment hostnames, filesystem paths, ports, process IDs, container IDs and service-manager
units remain deployment metadata. They never replace `node_*`, `worker_*`, `workspace_*`,
`artifact_*`, `task_*`, `run_*` or other canonical identities.

The checked-in profile loader in `ai_multi_agent_platform.deployment.advanced_profiles`
materializes canonical `NodeRecord`, `WorkerRecord` and `RegistrationRequest` objects from a
strict credential-free JSON profile. Machine-local binding metadata is kept in separate
`WorkerHostBinding` objects.

## Reference profiles

Credential-free examples are under `deploy/distributed/profiles/`:

| Profile | Purpose |
| --- | --- |
| `multi-local-workers.json` | one Control Plane host with two schedulable local Workers |
| `remote-worker.json` | one Control Plane plus one authenticated Worker on another device |
| `cpu-control-gpu-worker.json` | CPU/general host plus a generic accelerator-capable remote Worker |
| `heterogeneous-three-node.json` | general, accelerator/model-local and data-local Nodes |

The examples intentionally do not encode VPS plans, GPU vendors, cloud products, container
orchestration systems or operating-system-specific roles. Placement is derived from canonical
resources, capabilities, runtimes, models, labels, trust and locality.

## Capability-based role composition

Roles are emergent from Node/Worker facts rather than fixed machine names.

Examples:

- a job requiring no accelerator may run on any healthy Worker that satisfies CPU/RAM/storage,
  executor, runtime, capability and policy constraints;
- a job with `gpu="required"` and a VRAM floor is rejected from CPU-only Nodes and can be placed
  on any matching accelerator Node;
- `model_ref` constrains placement to Workers reporting that model;
- `capability_refs` can represent browser/tool/model-serving or application-specific execution
  abilities without creating hard-coded host roles;
- `locality_refs` influence placement when workspace, dataset, model or artifact locality is
  useful;
- drain, maintenance, trust and health remain explicit scheduler gates.

Equal candidates still use canonical Worker ID as the deterministic tie-break defined by #14.

## Registration and discovery

### Multiple local Workers

Local Workers register through the same canonical `RegistrationRequest` used by the distributed
runtime. A deployment may run multiple Worker processes on one Node; Node-wide CPU/RAM/storage
capacity accounting prevents them from independently overcommitting the same host resources.

Local process supervision is replaceable. systemd, Windows services, containers or another
service manager may start Workers, but the supervisor's process identity does not become the
Worker ID.

### Remote Workers

Remote reporters use `WorkerProtocolService` rather than an ad-hoc registration endpoint. The
reporter:

1. authenticates with the existing #36 Worker credential mechanism;
2. binds the authenticated identity to `RegistrationRequest.service_identity_ref`;
3. passes its credential scope and #15 authorization;
4. registers the complete Node/Worker snapshot;
5. sends monotonic authenticated heartbeats;
6. re-registers with the same canonical IDs after restart or reconnect.

Remote registration cannot self-grant Control-Plane-owned trust, drain or maintenance state.

The deployment JSON stores only a canonical `SecretReference` describing where the service
credential is provisioned. Raw credential material is neither valid profile state nor committed
to the repository.

## Worker transport status and adapter boundary

The canonical remote-job protocol is already implemented by `TransportWorkerDispatcher`,
`WorkerTransportEndpoint` and `WorkerTransportCodec` over the #35 `MessageTransport` contract.
The repository's dependency-free reference `InProcessMessageTransport` is intentionally an
in-process implementation for single-host operation and tests.

A real cross-host deployment therefore requires a conforming network-capable #35
`MessageTransport` adapter. Issue #240 does **not** redefine that transport contract or pretend
the in-process reference is a network broker. The profile field `transport_endpoint_ref` is a
deployment lookup reference for whichever approved adapter the operator wires into the
composition; it is not a canonical Worker or Node identifier.

Until such a network adapter is selected/configured, the remote profiles validate architecture,
security, registration and placement composition but are not by themselves a runnable
cross-machine daemon package. This is an explicit deployment dependency, not hidden fallback.

## Workspace, snapshots and artifacts

Only canonical references cross the Worker job boundary:

- `workspace_ref`;
- `snapshot_ref`;
- input/output `artifact_refs`;
- secret references.

A Control-Plane filesystem path must never be sent as a remote Worker workspace identity.
Each `WorkerHostBinding` declares a machine-local absolute `workspace_root`. The deployment
helper maps a canonical Workspace ID deterministically to:

```text
<workspace_root>/<workspace_id>
```

That local path remains adapter-private. #37 materialization/snapshot rules decide how content
is reconstructed or synchronized on the destination Worker.

## Network and exposure matrix

Ports are deployment choices, so examples use logical endpoint references instead of hard-coded
provider ports. The required exposure policy is:

| Flow | Default scope | TLS / identity | Notes |
| --- | --- | --- | --- |
| browser/client -> Control Plane | public only when explicitly enabled | TLS for public exposure; normal Control Plane auth | canonical northbound API only |
| Control Plane -> local Worker | loopback/in-process | local process boundary | no public listener required |
| Control Plane <-> remote Worker transport | private | authenticated TLS and Worker service identity | use conforming #35 network adapter |
| remote Worker registration/heartbeat | private | #36 Worker credential, replay protection, optional TLS-peer binding | through `WorkerProtocolService` |
| Worker -> workspace/artifact backing service | private when networked | authenticated service boundary | canonical refs, never Control-Plane paths |
| Worker -> local model endpoint | private/loopback | deployment-specific auth if required | optional and replaceable |
| browser/tool/connector service | private by default | scoped service identity/auth | optional; not a direct browser backend |
| SQLite/local filesystem stores | no listener | filesystem permissions | never exposed as network services |

No optional service becomes public merely because a deployment tool can publish a port.

## Failure and recovery behavior

### Missed heartbeats / network interruption

`DistributedRegistry.expire_heartbeats()` marks stale Nodes and Workers offline. The canonical
scheduler excludes them from new placement. Existing accepted jobs are reconciled through the
runtime rather than assumed to have stopped.

A network partition is not proof that remote execution ended. Cross-Worker failover therefore
uses the existing fencing contract before ownership moves to another Worker.

### Worker restart

For a normal Worker restart:

1. start the Worker with the same canonical Node/Worker IDs;
2. load the credential through the configured SecretReference provider;
3. establish the configured authenticated transport;
4. re-register the full Node/Worker snapshot;
5. resume heartbeats with a fresh request nonce;
6. let runtime reconciliation inspect any previously owned Worker Jobs.

Do not mint a replacement canonical Worker ID merely because a process restarted.

### Control Plane restart

Persisted health is not treated as fresh liveness evidence. Workers re-register/heartbeat and
re-establish reachability. Canonical dispatch ownership and reservation recovery remain owned by
#14 persistence/reconciliation semantics.

## Operator lifecycle

### Add a Worker

1. provision an operator-controlled canonical Worker ID;
2. define its Node/resource/capability declaration;
3. for remote Workers, provision a scoped #36 credential outside source control;
4. add only its `SecretReference` to deployment configuration;
5. start the transport/Worker process;
6. verify registration, health and scheduler eligibility through canonical Node/Worker views.

### Drain a Worker

Set canonical Worker drain state before planned maintenance. New jobs are rejected for that
Worker while existing ownership remains visible for reconciliation. Stop the process only after
accepted work has reached the operator's intended state.

### Remove or replace a Worker

Use explicit deregistration when permanently removing a Worker. Replacing hardware does not
require changing the Worker ID when the operator intends to preserve that same logical Worker
identity and the security/operational policy allows it; changing logical ownership should use a
new canonical ID.

### Rotate a Worker credential

1. create/activate the replacement scoped Worker credential through #36/#34;
2. update the deployment SecretReference if the secret locator/version changes;
3. restart/reload the Worker with the replacement credential;
4. re-register and verify heartbeat;
5. revoke the previous credential.

Never place the new token value in the profile, service unit, compose file or command history.

### Move an optional service endpoint

Update only the deployment endpoint reference/adapter configuration. Model/tool/browser service
addresses do not become canonical model, tool, Node or Worker IDs.

### Fall back to #39

Advanced components are extensions. An operator may remove remote Workers and optional services
and return to the single-server profile from #39 without migrating Task/Run identity or creating
a second canonical store.

## Optional services

The profile format can declare optional logical services such as a model endpoint, browser
service or connector service. Disabled services have no endpoint. Enabled services remain
replaceable deployment dependencies and are private by default.

Absence of an optional service does not invalidate unrelated Workers. If an enabled optional
service degrades, only capabilities depending on that service should become unavailable or
report degraded health; the whole platform must not be treated as failed when unaffected
capabilities remain usable.

The checked-in no-paid-service profile keeps model/browser/connector services disabled and uses
the local/reference execution path.

## Security and least privilege

Advanced profiles preserve the same security baseline as #39 and the distributed runtime:

- Worker credentials are scoped and replay-protected;
- raw secret values are not configuration state;
- remote communication requires authenticated TLS in the profile contract;
- remote Workers cannot overwrite administrative trust/drain/maintenance state;
- local workspace roots are writable only by the Worker identity that needs them;
- application code, credentials and service configuration remain outside untrusted Workspaces;
- optional service listeners are private/loopback unless explicitly justified;
- no direct browser access to Worker transports or backend-private services;
- logs/telemetry must preserve redaction rules.

## Validation and smoke coverage

`tests/test_issue240_advanced_deployment.py` verifies:

- all four reference profiles parse;
- deployment host references do not become canonical identities;
- plaintext credential fields are rejected;
- multiple local Workers register and use the canonical deterministic scheduler;
- remote profile TLS/SecretReference/reporter requirements;
- authenticated remote registration, re-registration and heartbeat;
- canonical dispatch through the Worker transport adapter boundary;
- heartbeat expiry prevents new placement;
- accelerator requirements exclude CPU-only Workers;
- drain state excludes a Worker;
- heterogeneous capability/locality selection;
- workspace paths remain machine-local while canonical refs survive the transport codec;
- optional services may all be absent;
- the #39 single-node baseline remains valid independently.

The transport dispatch test intentionally uses the in-process #35 reference transport as a
contract fixture. It proves the canonical message path, not cross-host socket reachability.
