# Distributed deployment reference profiles

This directory contains provider-neutral deployment examples for issue #240. They are
compositions over the canonical #14 distributed runtime, not a new Worker architecture.

## Profiles

- `profiles/multi-local-workers.json` — one ordinary host, one Control Plane, two local Workers.
- `profiles/remote-worker.json` — one Control Plane and one authenticated remote Worker.
- `profiles/cpu-control-gpu-worker.json` — CPU/general Worker plus a generic accelerator Worker.
- `profiles/heterogeneous-three-node.json` — three differently capable Nodes selected through
  resources, capabilities and locality.

All examples are credential-free. Reporter entries contain only `SecretReference` locator/scope
metadata, including local same-host reporters. Raw Worker tokens, passwords, API keys and private
keys are invalid profile content.

## Validate a profile

After installing the repository package, load a profile through the checked-in deployment
loader:

```bash
python - <<'PY'
from ai_multi_agent_platform.deployment import load_advanced_deployment_profile

profile = load_advanced_deployment_profile(
    "deploy/distributed/profiles/heterogeneous-three-node.json"
)
print(profile.profile_id)
for request in profile.registration_requests:
    print(request.node.node_id, request.node.worker_refs, [w.worker_id for w in request.workers])
PY
```

`registration_requests` are canonical #14 objects. `host_ref`, `transport_endpoint_ref` and
`workspace_root` remain deployment-only metadata and are not copied into Node/Worker identity.
The shipped Worker process binds the declared `reporter_worker_id` as the authenticated
`service_identity_ref` for both local and remote reporters.

## Runnable composition

Issue #240 ships three explicit operator entrypoints in addition to the unchanged #39
`platform-server` fallback:

- `platform-message-broker` — self-hosted network implementation of the existing #35
  `MessageTransport` contract;
- `platform-distributed-server` — normal Control Plane plus `DistributedRuntime`, authenticated
  Worker-protocol HTTP surface, scheduler attachment and remote Workspace materialization;
- `platform-worker` — one independently running Worker endpoint with registration/heartbeat,
  TCP command transport and Worker-local Workspace materialization.

The distributed server must be bound to one checked/validated topology:

```bash
platform-distributed-server \
  --profile deploy/distributed/profiles/remote-worker.json \
  serve
```

`PLATFORM_DISTRIBUTED_PROFILE` may be used instead of `--profile`. Startup rejects a profile that
contains a Node without a declared reporter or Worker credential reference; this prevents a
configuration example from being accepted as runnable when the shipped Worker process cannot
actually register it.

For a Worker topology:

1. provision each Node reporter's scoped #36 Worker credential outside source control with
   `platform --yes worker provision <reporter_worker_id> --secret-file <worker-token-file>`;
2. provision the #35 transport HMAC key and/or mTLS material outside source control;
3. start `platform-message-broker` on the selected loopback/private endpoint;
4. configure `PLATFORM_MESSAGE_BROKER_HOST` and `PLATFORM_MESSAGE_BROKER_PORT` for
   `platform-distributed-server` and start it with the same `--profile` used by the Workers;
5. expose the Control Plane Worker-protocol route through HTTPS for a non-loopback Worker;
6. inject the actual Worker credential from the selected secret file only in the reporter Worker
   process environment and run `platform-worker --profile ... --host-ref ... --worker-id ...
   --control-plane-url ... --broker-host ... --broker-port ...`;
7. start sibling Worker processes for the same Node with their own `--worker-id`; only the
   declared reporter performs Node registration/heartbeat;
8. let authenticated registration attach `TransportWorkerDispatcher`,
   `TransportRemoteWorkspaceMaterializer` and `MaterializingWorkerDispatcher` to the same
   canonical distributed runtime used by ordinary Task/Run execution.

The Worker process starts both `WorkerTransportEndpoint` and
`WorkerWorkspaceTransportEndpoint`. Canonical Workspace/snapshot/artifact references cross the
transport; the destination machine reconstructs Workspace/File content beneath its own configured
host-level `workspace_root` and a private child directory for that Worker identity.

A normal Control Plane Task does not need distributed-specific Task logic. With the advanced
composition enabled, the existing kernel `LifecycleBackend` seam routes the canonical Run into
`DistributedRuntime`; the canonical scheduler chooses an eligible Worker and the terminal Worker
snapshot is reconciled back into the same Run/Task lifecycle.

`TcpMessageTransport` is the checked-in network-capable #35 adapter. Loopback operation may run
without TLS; non-loopback TCP connections/listeners fail closed unless TLS is configured. The
Worker-protocol HTTP client likewise requires HTTPS for non-loopback Control Plane URLs.

## Multiple Worker processes on one Node

One profile Node may declare several Workers. Exactly the declared `reporter_worker_id` owns the
complete authenticated registration/heartbeat snapshot. Additional Worker processes for that
Node run their own execution/Workspace endpoints without inventing a second Node reporter. This
keeps Node-wide capacity and liveness authority canonical while allowing separate OS processes.

Local Workers use the same #36 Worker identity and #14 registration semantics as remote Workers.
`connection_mode = "local"` changes deployment locality/TLS expectations only; it does not create
an unauthenticated local Worker model. The reference local profiles therefore use loopback TCP,
a reporter and a credential reference rather than an unused in-process transport declaration.

The Node-level `workspace_root` is only a host parent. `platform-worker` derives
`<workspace_root>/<worker_id>` for every independent process. Sibling Workers therefore cannot
share, replace or clean up the same materialization tree by accident.

## Workspace rule

Each Worker host declares one absolute machine-local `workspace_root`. Worker jobs carry only
workspace, snapshot and artifact references; they never carry the Control Plane's local
filesystem path. #37 materialization transfers the exact canonical snapshot to a Worker-local
`<workspace_root>/<worker_id>/<workspace_id>/<snapshot_id>` execution tree and collects changed
files back through the canonical File boundary.

`artifact_refs` remain opaque canonical identifiers across Worker transport. Artifact content is
not inferred from an `artifact_*` ID; content required by an executor must be reachable through the
canonical Workspace/File materialization boundary rather than a deployment-local artifact store.

When a normal Run already has a canonical `RunWorkspaceBinding`, the distributed lifecycle adapter
copies only its canonical Workspace ID and exact snapshot ID into the Worker Job. No local path is
added to Task/Run state.

## Security defaults

- Remote Worker bindings require TLS.
- Local same-host reporters still require #36 Worker authentication; loopback transport may omit
  TLS when the deployment remains loopback-only.
- Registration uses #36 Worker authentication and #15 authorization for both local and remote
  reporters.
- TCP transport supports scoped deployment HMAC authentication and TLS/mTLS.
- Credential values are read only from operator-selected runtime environment variables or TLS
  files; committed profiles contain references only.
- Optional model/browser/tool/connector services are private by default.
- SQLite and local filesystem stores expose no network listener.
- Do not put credential values in JSON, service definitions, compose files or command arguments
  committed to the repository.

See `docs/ADVANCED_DEPLOYMENT.md` for the network matrix, placement examples, failure behavior,
credential rotation, drain/restart/replacement operations, reproducible two-machine flow and the
#39 fallback path.
