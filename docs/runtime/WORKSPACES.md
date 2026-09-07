# Workspace and Project Environment Contract

Issue: #37

## Purpose

The platform owns workspace identity and lifecycle. Executors, source connectors and remote workers must not invent incompatible workspace semantics or treat host filesystem paths as canonical IDs.

A workspace is a portable project/task/run working context identified by `workspace_*`. Its immutable content states are identified by `workspace_snapshot_*`. Local and remote execution copies are disposable materializations and never replace the durable workspace/snapshot identity.

## Canonical models and boundaries

`ai_multi_agent_platform.workspaces` defines:

- `Workspace` — canonical project/owner identity, type, access mode, status, revision, source references, head snapshot, retention and active task/run references.
- `WorkspaceSnapshot` — immutable content manifest with deterministic checksum, canonical `file_*` references, provenance, parent snapshot and optional artifact/source revision references.
- `WorkspaceMaterialization` — one bounded execution copy tied to an exact snapshot and base revision; `execution_workspace` is an opaque local token rather than a host path.
- `WorkspaceChangeSet` — created/modified/deleted paths with changed bytes returned through canonical `FileProvider` references.
- `WorkspaceProvider` — replaceable platform-owned lifecycle/materialization boundary.
- `RunWorkspaceBinding` — immutable canonical record linking a Run to the exact `workspace_*`, `workspace_snapshot_*` and snapshot checksum selected as its input.

The platform Control Plane accepts a canonical `WorkspaceProvider`. When configured, `/api/v1/workspaces` reads and creates canonical Workspaces rather than the historical identity-only placeholder. Existing extension services and command handlers continue to compose through the same Control Plane boundary.

## Workspace types

The contract has explicit semantics for:

- persistent project workspaces;
- ephemeral task workspaces;
- isolated run workspaces;
- read-only source workspaces;
- cloned workspaces;
- remote workspaces/materializations.

These types do not imply a particular Git provider, container engine, VPS layout, operating system or storage product.

## Source attachment

`WorkspaceSourceRef` is provider-neutral and can represent empty sources, canonical files, snapshots, artifacts, repositories and templates. Authentication and source-control behavior remain connector responsibilities and do not redefine workspace identity.

`WorkspaceSourceResolverRegistry` is the extension boundary for turning source references into canonical `WorkspaceFile` manifests. The local platform includes:

- `EmptyWorkspaceSourceResolver`;
- `SnapshotWorkspaceSourceResolver`, including optional revision/checksum verification.

Repository, artifact and template connectors register resolvers for their source kinds later. If no resolver exists, the operation is explicitly unavailable rather than silently falling back to host files. Resolution of multiple sources rejects overlapping relative paths.

## Local materialization flow

`LocalWorkspaceProvider` implements the deterministic reference path:

1. resolve canonical workspace and exact snapshot IDs;
2. verify the snapshot manifest checksum;
3. resolve canonical `file_*` entries through `FileProvider`;
4. allocate a unique local materialization directory;
5. validate all relative paths before writing;
6. verify source checksums after materialization;
7. apply read/write policy;
8. execute through an opaque materialization token;
9. scan for changed/deleted files;
10. return changed bytes through `FileProvider` and expose canonical file IDs in the change set;
11. commit only against the expected workspace revision;
12. release/clean the disposable materialization.

`materialization_root` and `local_path()` are reference-implementation helpers for local execution/tests. They are not canonical northbound or distributed identities.

## Isolation rules

The canonical path validator rejects absolute paths, `..` traversal, empty or `.` segments, backslash alternate separators and drive-style prefixes.

The local provider additionally rejects symlink escapes before change capture. Each materialization has its own directory, so isolated runs do not share a write root by default. Read-only materializations are filesystem-protected and change capture independently rejects mutation, preserving the semantic boundary even if local permission bits are bypassed.

## Snapshots, persistence and reproducibility

Snapshot checksums are deterministic over the sorted tuple of relative path, canonical file ID and file SHA-256. Re-materializing the same snapshot therefore resolves the same canonical content while using a different disposable local directory.

`SqliteWorkspaceProvider` persists canonical Workspace metadata, head snapshot identity and complete historical WorkspaceSnapshot manifests. Workspace/snapshot IDs, revisions, source provenance, retention/access metadata and file manifests survive process restart. Local materialization paths do not survive as canonical state; stale active materialization references are cleared and crash-orphan directories remain cleanup candidates.

A workspace revision changes only when a change set is committed. Additional snapshots of unchanged content may have different snapshot IDs while retaining the same content checksum and revision.

## Run input binding

A workspace-aware Run is bound before execution starts. The Control Plane resolves and validates the selected Workspace and exact WorkspaceSnapshot, checks that the Workspace belongs to the Task project, then persists a `RunWorkspaceBinding` before dispatching the Run.

The binding records:

- Run ID;
- Task ID;
- Workspace ID;
- exact WorkspaceSnapshot ID;
- snapshot content checksum.

`SqliteRunWorkspaceBindingRepository` makes that binding restart-safe and immutable. Reusing a Run ID with a different workspace/snapshot target is rejected. A retry inherits the previous exact snapshot by default unless a new explicit binding is supplied. Run API resources expose the workspace ID, snapshot ID and checksum so clients can inspect the reproducible input.

The binding is a canonical adjunct record rather than a host-materialization field. Ordering is deliberate: a Run may be queued before a binding write completes, but it is not started until its durable binding has succeeded. Retrying the same idempotent start recovers the same queued Run and completes the binding before dispatch.

## Concurrency

Every materialization records the workspace revision on which it was based. `commit_changes(..., expected_revision=...)` rejects stale writers with the canonical `CONFLICT` error, preventing silent overwrite by concurrent runs.

The reference implementation deliberately uses isolated-copy plus optimistic revision checking. Future shared-write leases/locks can be added without changing Workspace or WorkspaceSnapshot identity.

## Cleanup and retention

Local materializations are disposable execution copies. `release_materialization()` removes them for success, failure or cancellation; `cleanup()` detects missing known materializations and removes orphaned local materialization directories without deleting canonical FileProvider objects or WorkspaceSnapshots.

`RetentionManagedWorkspaceProvider` adds deterministic lifecycle policy over any `WorkspaceProvider`:

- `persistent` workspaces are retained;
- `ephemeral` workspaces become eligible only after they have actually been materialized;
- `until` workspaces become eligible at an explicit timezone-aware expiration instant;
- active materializations and active task/run references defer deletion;
- materialization and retention decisions are serialized so cleanup cannot race an in-progress materialization;
- `WorkspaceRetentionGuard` provides a policy/quota hook that can defer cleanup;
- retention tombstones can be persisted in SQLite;
- deleting a Workspace lifecycle identity never silently deletes its canonical snapshots or file objects.

Cleanup failures are represented in reports rather than silently treated as success.

## Distributed extension

`RemoteMaterializationRequest` carries the canonical workspace ID, snapshot ID, expected checksum, access mode and cache key required by a future worker transport.

The #37 boundary also defines:

- `RemoteMaterializationReceipt` — worker acknowledgement of the exact snapshot actually materialized, including checksum verification and opaque worker/materialization references;
- `RemoteMaterializationResult` — changed canonical File references and artifact references returned from remote execution;
- `RemoteCleanupAcknowledgement` — explicit success/failure acknowledgement for disposable worker-local cleanup;
- `RemoteWorkspaceMaterializer` — transport-neutral materialize/result/cleanup interface.

Remote receipts reject checksum mismatch and path-like materialization references. #14 can implement actual worker transfer, caching and transport over these contracts without redefining Workspace identity.

## Follow-up integration boundaries

The workspace domain is complete without requiring concrete implementations of later subsystems:

- #14 supplies actual remote-worker transport/materialization execution;
- #15 supplies authorization enforcement around the existing workspace policy context;
- #34 supplies secret references for authenticated external sources;
- #44 supplies concrete repository/artifact/template source connectors/resolvers;
- #17 can expose richer workspace/project UI over the canonical Control Plane.

Those integrations consume the #37 contracts rather than changing their identities or lifecycle semantics.

## Acceptance coverage

The implementation and tests cover:

- canonical path-independent Workspace and WorkspaceSnapshot IDs;
- persistent, ephemeral, isolated-run and read-only semantics;
- bounded local executor materialization;
- exact restart-safe Run → WorkspaceSnapshot bindings;
- traversal and symlink escape rejection;
- read-only mutation rejection;
- deterministic snapshot integrity verification;
- stale/concurrent update conflicts;
- cancellation/release and crash-orphan cleanup;
- deterministic retention and expiration with policy/quota guard hooks;
- missing canonical source/reference handling;
- canonical changed-file/artifact return contracts;
- repeated materialization of the same canonical snapshot;
- restart-safe Workspace/Snapshot metadata;
- source-resolver extension boundaries;
- remote materialization/result/cleanup acknowledgement contracts.

The local/reference test path requires neither remote workers nor source-control credentials.
