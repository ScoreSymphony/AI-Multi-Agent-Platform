# Remote Workspace materialization

Issue #433 provides the concrete distributed implementation of the Workspace contracts from #37 on top of the platform-owned MessageTransport from #35.

The implementation lives in the public module:

```python
from ai_multi_agent_platform.distributed.workspace_transport import (
    TransportRemoteWorkspaceMaterializer,
    WorkerWorkspaceMaterializationStore,
    WorkerWorkspaceTransportEndpoint,
    WorkspaceBoundLocalWorker,
)
```

The module is intentionally not re-exported from `ai_multi_agent_platform.distributed`. Importing it eagerly from the package root would pull the portability graph into every distributed import and creates an avoidable circular dependency. Runtime composition should import the concrete module directly.

## Boundary

The Control Plane remains authoritative for canonical Workspace, WorkspaceSnapshot and File identity. Remote materialization transfers only portable identifiers, manifest metadata and file bytes. Control-Plane host paths, Worker-local paths and provider storage paths are not serialized into the distributed contract.

The Worker chooses a deterministic local materialization root below its configured Worker root:

```text
<worker-root>/<workspace-id>/<snapshot-id>/
```

That path is a Worker-local implementation detail. Execution is bound through the canonical `workspace_ref` and `snapshot_ref` carried by the Worker job.

## Components

### `TransportRemoteWorkspaceMaterializer`

Control-side implementation of `RemoteWorkspaceMaterializer`. It:

- resolves the canonical Workspace and Snapshot;
- verifies the requested checksum against canonical snapshot state;
- reads canonical File bytes through `FileProvider`;
- sends a manifest and bounded chunks over `MessageTransport`;
- receives a Worker acknowledgement only after verification and installation;
- collects changed bytes back into canonical File records;
- requests outcome-aware cleanup.

### `WorkerWorkspaceMaterializationStore`

Worker-local state for incoming transfers and installed snapshots. It owns no canonical Workspace state. It:

- verifies the manifest checksum before accepting a transfer;
- rejects traversal and symlink escapes;
- verifies exact file length and SHA-256 before commit;
- installs only below the configured Worker root;
- makes read-only Workspace materializations read-only;
- records portable manifest state for validation and retry;
- treats duplicate prepare, chunk and completed commit delivery idempotently;
- rejects conflicting duplicate chunks;
- removes materializations idempotently for terminal outcomes.

### `WorkerWorkspaceTransportEndpoint`

Consumes Workspace commands for exactly one Worker through the #35 transport. A command is acknowledged only after its operation reply is published. If reply publication fails, the command is negatively acknowledged for redelivery. Because the Store operations are idempotent, a lost reply does not require a duplicate materialization.

### `WorkspaceBoundLocalWorker`

Binds a canonical Worker job to the already materialized Workspace identified by its `workspace_ref` and `snapshot_ref`, then creates the local lifecycle backend for that exact Worker-local execution token.

## Wire flow

```text
Control Plane                         Worker
     |                                  |
     | workspace.prepare                |
     |--------------------------------->|
     | workspace.prepare.accepted       |
     |<---------------------------------|
     |                                  |
     | workspace.put_chunk x N          |
     |--------------------------------->|
     | workspace.put_chunk.accepted     |
     |<---------------------------------|
     |                                  |
     | workspace.commit                 |
     |--------------------------------->|
     | verify + install                 |
     | workspace.commit.accepted        |
     |<---------------------------------|
     |                                  |
     | dispatch Worker job              |
     |--------------------------------->|
     |                                  |
     | workspace.result_manifest/chunk  |
     |<-------------------------------->|
     | canonicalize changed Files       |
     |                                  |
     | workspace.cleanup                |
     |--------------------------------->|
```

## Retry and idempotency

Each Control-side operation carries a deterministic transport idempotency key. Worker-side semantics additionally protect execution when a command itself is redelivered:

- repeated `prepare` with identical metadata returns the same materialization reference;
- repeated identical chunks are no-ops;
- a repeated chunk with different bytes is rejected;
- repeated `commit` after a successful commit validates the persisted manifest and installed tree, then returns a cache receipt;
- repeated cleanup succeeds when the materialization is already absent.

This is required because transport acknowledgement and operation execution are separate events: a Worker can commit successfully and lose the reply before the command is acknowledged.

## Security invariants

- Relative Workspace paths are validated before filesystem access.
- Resolved targets must remain below the configured Worker root/materialization root.
- Symlinks are rejected both during installation and result scanning.
- File bytes are accepted only when their size and SHA-256 match the canonical manifest.
- Read-only materializations reject changed or deleted result state.
- Transport envelopes contain canonical identifiers and portable data, not host-local paths.
- Cross-host deployments inherit TLS/authentication requirements from the selected `MessageTransport` implementation.

## Runtime composition

A Worker runtime composes one local Store and one transport endpoint per Worker identity. The Control Plane composes `TransportRemoteWorkspaceMaterializer` with the same Worker identity and its configured `MessageTransport`. The higher-level Worker daemon and deployment composition remain owned by the distributed runtime/worker composition work; this module supplies the concrete Workspace transfer boundary rather than defining a second runtime authority.
