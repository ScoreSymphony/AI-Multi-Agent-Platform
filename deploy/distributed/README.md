# Distributed deployment reference profiles

This directory contains provider-neutral deployment examples for issue #240. They are
compositions over the canonical #14 distributed runtime, not a new Worker architecture.

## Profiles

- `profiles/multi-local-workers.json` — one ordinary host, one Control Plane, two local Workers.
- `profiles/remote-worker.json` — one Control Plane and one authenticated remote Worker.
- `profiles/cpu-control-gpu-worker.json` — CPU/general Worker plus a generic accelerator Worker.
- `profiles/heterogeneous-three-node.json` — three differently capable Nodes selected through
  resources, capabilities and locality.

All examples are credential-free. Remote entries contain only `SecretReference` locator/scope
metadata. Raw Worker tokens, passwords, API keys and private keys are invalid profile content.

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

## Composition rules

For a local Worker, bind the declared Worker ID to a `LocalWorker` or another conforming
`WorkerDispatcher` and attach it to the existing `DistributedRuntime`.

For a remote Worker:

1. provision the credential referenced by `credential_reference` outside source control;
2. establish an authenticated TLS-capable deployment transport;
3. register/heartbeat through `WorkerProtocolService`;
4. expose execution through `WorkerTransportEndpoint` on a conforming #35 `MessageTransport`;
5. attach the Control-Plane side through `TransportWorkerDispatcher`;
6. let the canonical scheduler perform placement.

The repository currently provides `InProcessMessageTransport` as the dependency-free reference
transport for same-process operation and tests. It is **not** a cross-machine network transport.
A real remote deployment must supply a conforming network-capable #35 adapter; the profile's
`transport_endpoint_ref` selects deployment configuration for that adapter without making the
adapter canonical.

## Workspace rule

Each Worker host declares one absolute machine-local `workspace_root`. A canonical
`workspace_*` ID maps deterministically beneath that root. Worker jobs carry only workspace,
snapshot and artifact references; they never carry the Control Plane's local filesystem path.
Content materialization remains governed by #37.

## Security defaults

- Remote Worker bindings require TLS.
- Remote registration uses #36 Worker authentication and #15 authorization.
- Optional model/browser/tool/connector services are private by default.
- SQLite and local filesystem stores expose no network listener.
- Do not put credential values in JSON, environment examples, service definitions or compose
  files committed to the repository.

See `docs/ADVANCED_DEPLOYMENT.md` for the network matrix, placement examples, failure behavior,
credential rotation, drain/restart/replacement operations and the #39 fallback path.
