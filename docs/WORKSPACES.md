# Workspace and Project Environment Contract

Issue: #37

## Purpose

The platform owns workspace identity and lifecycle. Executors, source connectors and future remote workers must not invent incompatible workspace semantics or treat host filesystem paths as canonical IDs.

A workspace is a portable project/task/run working context identified by `workspace_*`. Its immutable content states are identified by `workspace_snapshot_*`. A local or remote execution copy is a temporary `materialization_*` and is never the durable identity of the workspace.

## Canonical models

`ai_multi_agent_platform.workspaces` defines:

- `Workspace`
  - canonical workspace/project/owner identity;
  - workspace type and access mode;
  - lifecycle status;
  - optimistic revision;
  - source references;
  - current base snapshot;
  - retention and active task/run references.
- `WorkspaceSnapshot`
  - immutable workspace content manifest;
  - content checksum;
  - canonical `FileProvider` references;
  - parent snapshot and source provenance;
  - optional artifact/source revision references.
- `WorkspaceMaterialization`
  - one isolated execution copy bound to an exact snapshot and base revision;
  - an opaque executor-local token (`execution_workspace`), not a host path.
- `WorkspaceChangeSet`
  - created/modified files returned as canonical `file_*` evidence;
  - deletions represented explicitly;
  - base revision retained for conflict detection.
- `RemoteMaterializationRequest`
  - the stable canonical information a future worker transport needs to fetch/materialize a snapshot without redefining workspace identity.

## Workspace types

The contract reserves explicit types for:

- persistent project workspaces;
- ephemeral task workspaces;
- isolated run workspaces;
- read-only source workspaces;
- cloned workspaces;
- remote materializations.

The type does not imply a specific Git provider, container engine, VPS layout or operating system.

## Source attachment

`WorkspaceSourceRef` is provider-neutral and can describe empty/template sources, canonical files, snapshots, artifacts and repositories. Repository authentication and source-control operations remain connector responsibilities; they do not redefine the workspace domain.

The local reference implementation currently materializes canonical `WorkspaceFile` entries from the platform `FileProvider`. Repository/template/artifact resolvers can be added behind the same contracts later.

## Local materialization flow

`LocalWorkspaceProvider` implements the reference path:

1. verify canonical workspace and snapshot IDs;
2. verify the snapshot manifest checksum;
3. resolve canonical `file_*` entries through `FileProvider`;
4. create a unique local `materialization_*` directory;
5. validate all relative paths before writing;
6. verify source checksums after materialization;
7. apply read-only filesystem permissions when required;
8. execute through the opaque materialization token;
9. scan for changed/deleted files;
10. return changed bytes to `FileProvider` and expose only canonical file IDs in the change set;
11. commit with optimistic workspace revision checks;
12. release/clean the local materialization.

`materialization_root` and `local_path()` are deliberately reference-implementation helpers. They are local details for wiring the existing `ReferenceExecutor` and tests, not northbound or distributed identities.

## Isolation rules

The canonical relative-path validator rejects:

- absolute paths;
- `..` traversal;
- empty or `.` path segments;
- backslash-based alternate separators;
- drive-style prefixes.

The local implementation additionally rejects symlinks in materialized workspace trees before change capture. Every materialization has its own directory, so concurrent runs do not share a write root by default.

Read-only materializations are chmod-protected locally and change capture independently rejects any detected mutation. The latter preserves the semantic boundary even when local filesystem permissions are bypassed by a privileged process.

## Snapshots and reproducibility

Snapshot checksums are deterministic over the sorted tuple of:

`relative path + canonical file ID + file SHA-256`

Repeated materialization of the same snapshot therefore resolves the same canonical content while using different local directories.

A workspace revision changes only when a change set is committed. Additional snapshots of unchanged content may have different snapshot IDs but the same content checksum and revision.

## Concurrency

Each materialization records the workspace revision on which it was based. `commit_changes(..., expected_revision=...)` rejects stale writers with the canonical `CONFLICT` error. A second run cannot silently overwrite a revision already committed by another run.

The current reference implementation intentionally chooses isolated-copy plus optimistic revision checking. Shared-write leases/locks can be added later without changing canonical workspace or snapshot IDs.

## Cleanup and retention

Local materializations are ephemeral execution copies and are removed by `release_materialization()` for success, failure or cancellation. `cleanup()` reports missing known materializations and removes orphaned local `materialization_*` directories without touching canonical FileProvider objects.

Canonical workspace retention (`persistent`, `ephemeral`, `until`) is part of the model. Full scheduled expiration/quota enforcement belongs to the remaining #37 lifecycle integration work.

## Executor integration

The existing `ReferenceExecutor` can be pointed at `LocalWorkspaceProvider.materialization_root` and receives only `WorkspaceMaterialization.execution_workspace`. It therefore executes inside a provider-created bounded directory rather than receiving a canonical workspace as a host path.

A future executor/worker adapter should receive the canonical workspace/snapshot binding plus materialization instructions and construct its own local execution token.

## Distributed extension

Remote workers can consume `RemoteMaterializationRequest` containing:

- workspace ID;
- snapshot ID;
- expected checksum;
- access mode;
- cache key.

Future #14 transport adds snapshot transfer/fetch, worker-local caching, verification, result return and cleanup acknowledgement. None of those operations may replace the canonical workspace or snapshot identity with a worker path.

## Current implementation boundary

This first #37 implementation slice provides:

- canonical workspace/snapshot/materialization/change models;
- replaceable `WorkspaceProvider` lifecycle contract;
- deterministic local FileProvider-backed materialization;
- traversal and symlink isolation;
- read-only mutation detection;
- snapshot checksums;
- isolated concurrent materializations;
- optimistic stale-write conflict rejection;
- changed-file return through canonical FileProvider references;
- cancellation/release cleanup and orphan cleanup reporting;
- remote materialization request hooks;
- reference-executor integration coverage.

Still required before #37 can close:

- replace the Control Plane `WorkspaceIdentity` placeholder/`ScopeStore` workspace internals with the canonical workspace service;
- bind exact workspace/snapshot references into canonical Run state/events so every run records its reproducible input;
- persist workspace/snapshot metadata across process restart rather than keeping reference metadata in memory;
- implement canonical workspace expiration/quota cleanup policy execution;
- add connector-backed repository/artifact/template source resolvers;
- finalize #14 remote-worker materialization response/acknowledgement integration;
- perform final acceptance-criteria and architecture-conformance review.
