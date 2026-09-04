# Repository and Git Integration

Issue: #82

## Purpose

Repository access is a platform capability built on the existing connector, capability, file, workspace, security and event contracts. GitHub, GitLab, Gitea and local Git are providers or adapters; none of them own the platform's Agent, Task, Run or Workspace identity model.

## Canonical identity

A repository is represented by `RepositoryReference`, which wraps the connector-owned `ExternalResourceReference`:

- `external_resource_*` is the platform-owned canonical repository identity.
- `connection_*` identifies the configured connector/account/endpoint.
- provider-native repository IDs are kept only in `ExternalNativeReference`.
- local clone paths are adapter-private implementation data and never canonical IDs.
- credentials remain `SecretReference` values owned by the configured `Connection` and are not serialized in repository references.

## Provider seam

`RepositoryProvider` is the replaceable repository contract. The current baseline implementation is `LocalGitRepositoryProvider`, which uses the system Git executable without adding a Python runtime dependency.

The contract covers repository discovery/read, exact revision resolution, tree reads, branch/tag inspection, status/diff, branch creation, checkout, commit, fetch and push. Provider-specific issue/change-request APIs stay outside canonical repository types and are exposed by connector capabilities when a provider supports them.

## Capability and policy model

Repository operations publish explicit #12 capability metadata.

- Pure reads (`read`, ref inspection, status, diff) have no side effect.
- `fetch`, checkout, branch creation and commit mutate local state.
- `push` and provider-side issue/change-request writes are external side effects.
- provider credentials are referenced, never embedded.

Callers that mutate repositories should go through `RepositoryService`; it maps repository actions onto the existing #15 authorization/approval gate before invoking the provider. In particular, a denied push never reaches the Git adapter.

This is intentionally separate from executor shell access. An executor must not be treated as an authorization bypass for repository operations.

## Workspace materialization

`RepositoryWorkspaceSourceResolver` implements the existing `WorkspaceSourceKind.REPOSITORY` extension point.

The resolution path is:

1. Resolve canonical `RepositoryReference` through `RepositoryRegistry`.
2. Resolve the requested branch/tag/ref to an immutable Git commit SHA.
3. Read that exact tree through `RepositoryProvider`.
4. Store each file through the canonical #13 `FileProvider`.
5. Return canonical `WorkspaceFile` objects to #37 Workspace materialization.

Each generated file records repository ID, resolved revision, repository-relative path and provider ID in metadata. Repository-local filesystem paths do not cross this boundary.

Symlinks and non-file Git tree entries are currently rejected by the Local Git reference adapter rather than silently materialized with ambiguous filesystem semantics.

## Events and deduplication

`RepositoryEventBridge` converts verified #44 `ConnectorEvent` evidence into canonical platform `Event`s.

External repositories do not become new core Event subject types. A repository event is scoped to its canonical Project subject and carries the repository ID, connection ID, external resource IDs and connector provenance in the payload/external refs.

The canonical Event ID is deterministic over connection, connector type and the connector event's `dedupe_key`. The existing `EventProvider.publish` contract is idempotent by event ID, so repeated webhook deliveries cannot create duplicate canonical events.

Unverified connector events are rejected by default.

## Run provenance

`RepositoryRunProvenance` captures the repository-specific execution evidence needed by #82:

- Run and optional Task ID
- canonical repository ID
- immutable input revision
- optional branch/ref
- optional output revision
- actor/agent reference
- diff Artifact IDs
- provider resource IDs

`RepositoryProvenanceStore` is the initial platform-owned seam. Runtime lifecycle wiring that records this automatically at Run start/completion remains part of the follow-up work for #82.

## Current Local Git baseline

Covered now:

- initialize/open
- branch creation and checkout
- branch/tag inspection
- status and diff
- commit
- exact revision lookup
- exact tree reads
- fetch and push primitives
- canonical error translation
- missing Git handling
- exact-revision Workspace materialization
- provider replacement without canonical identity changes
- SecretReference/path isolation
- policy-denied push
- verified event normalization and deterministic deduplication

## Remaining #82 integration work

This first slice deliberately does not close #82. Remaining work includes:

- hosted/self-hosted provider bridge(s) through #44 connector providers
- full Control Plane repository resources and commands
- CLI and Web UI repository surfaces
- automatic Run start/completion provenance wiring
- changed-file/diff Artifact creation after Workspace execution
- optional commit/push execution flow after Run completion
- end-to-end provider event -> automation trigger coverage
- persistence/registration bootstrap for repository bindings
- broader conformance, failure and provider-replacement tests

These additions must preserve the invariant that provider-specific types and raw clone paths do not become canonical platform identities.
