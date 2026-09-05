# Repository and Git Integration

Issue: #82

## Purpose

Repository access is a provider-neutral platform capability built on the existing connector, capability, file, workspace, security, event and automation contracts. GitHub, GitLab, Gitea, other hosted/self-hosted forges and local Git are providers or adapters; none of them own the platform's Agent, Task, Run, Project or Workspace identity model.

## Canonical identity

A repository is represented by `RepositoryReference`, which wraps the connector-owned `ExternalResourceReference`:

- `external_resource_*` is the platform-owned canonical repository identity;
- `connection_*` identifies the configured connector/account/endpoint;
- provider-native repository IDs remain only in `ExternalNativeReference` with an explicit namespace;
- local clone paths are adapter-private implementation data and never canonical IDs;
- credentials remain `SecretReference` values owned by the configured `Connection` and are not serialized into repository references, Tasks or Agent definitions.

`RepositoryConnection` wraps the canonical connector `Connection` instead of defining a second credential/account model.

## Provider seam

`RepositoryProvider` is the replaceable repository contract. It covers repository discovery/read, exact revision resolution, tree reads, branch/tag inspection, status/diff, branch creation, checkout, commit, fetch and push.

Two provider paths are available:

1. `LocalGitRepositoryProvider` uses the system Git executable and works without an external service or additional Python Git dependency.
2. `ConnectorRepositoryProvider` adapts the generic #44 `ConnectorProvider` contract for hosted or self-hosted repository services. Connector-native resource/action payloads are validated and projected back into canonical repository models before crossing the repository boundary.

The bridge is intentionally forge-neutral. Adding a GitHub, GitLab, Gitea or another connector does not change Agent, Task, Run or Workspace contracts.

## Capability model

`RepositoryOperation` and `repository_capability_specs()` define #12-compatible capability contracts for:

- discovery/read/materialization;
- fetch and ref inspection;
- branch/checkout/status/diff/commit/push;
- issue read/write;
- change-request (PR/MR) read/write;
- repository event receipt.

Not every provider must implement every operation. Repository references advertise the operations supported by the concrete adapter, while the global capability definitions provide the provider-neutral contract for optional collaboration features.

Side effects are classified independently of provider identity:

- pure reads (`read`, ref inspection, status, diff) have no side effect;
- `fetch`, checkout, branch creation and commit mutate local state;
- `push` and provider-side issue/change-request writes are external side effects;
- credential-requiring operations declare that requirement without exposing secret values.

## Authorization and approvals

`RepositoryService` is the policy-enforced facade. Callers do not invoke Git/provider binaries or APIs directly through this repository surface.

The service maps operations onto #15 authorization/approval semantics:

- read/status/diff: standard read;
- fetch: external read plus local write;
- create branch/checkout/commit: elevated local mutation;
- push: high-risk external mutation.

A denied operation fails before the provider adapter is invoked. Approval IDs can be propagated through the Control Plane command surface.

This boundary is deliberately separate from unrestricted shell execution. Executor access must not be treated as an authorization bypass for repository operations.

## Workspace materialization

`RepositoryWorkspaceSourceResolver` implements the existing `WorkspaceSourceKind.REPOSITORY` extension point.

The resolution path is:

1. resolve the canonical `RepositoryReference` through `RepositoryRegistry`;
2. resolve the requested branch/tag/ref to an immutable Git commit SHA;
3. read that exact tree through `RepositoryProvider`;
4. store each file through the canonical #13 `FileProvider`;
5. return canonical `WorkspaceFile` objects to #37 Workspace management;
6. persist the resolver-returned source reference so the Workspace records the immutable SHA rather than the originally requested moving ref.

Each generated file records repository ID, resolved revision, repository-relative path and provider ID in metadata. Repository-local filesystem paths do not cross this boundary.

The Local Git adapter rejects symlink/non-blob tree entries during canonical materialization rather than silently introducing ambiguous filesystem semantics.

## Run provenance and changed-file artifacts

`RepositoryRunProvenance` records:

- Run and optional Task ID;
- canonical repository ID;
- immutable input revision;
- optional branch/ref;
- optional output revision;
- actor and optional canonical Agent ID;
- changed-file/diff Artifact IDs;
- provider resource IDs.

`RepositoryRunProvenanceMixin` can be configured on the composed Control Plane. For workspace-aware task start/retry it records the exact repository input after the immutable `RunWorkspaceBinding` is established and before execution dispatch. Repeated/retried runs therefore retain their actual input SHA even when a symbolic branch later moves.

`RepositoryRunIntegration` consumes the existing #37 execution materialization/change-set boundary. It creates deterministic changed-file Artifacts plus a canonical JSON change-manifest Artifact, upserts those artifact references into Run provenance, and can record the resulting commit SHA as output revision. Recording a commit does not imply that a push occurred.

`SqliteRepositoryProvenanceStore` provides restart-safe provenance persistence. It stores canonical IDs, immutable revisions and artifact/resource references only; provider instances, clone paths and credentials are not serialized.

## Durable repository registration

`SqliteRepositoryBindingCatalog` persists provider-neutral repository routing metadata across process restarts. `RepositoryRegistryBootstrap` reconstructs the in-process registry by combining catalog records with:

- a canonical `Connection` resolver;
- a provider factory registered by provider ID.

Live provider objects are never serialized. For connector-backed repositories, credentials are reobtained through the canonical `Connection` and its `SecretReference`s. A missing provider factory fails closed with `UNAVAILABLE` instead of silently substituting another implementation.

The Local Git bootstrap factory keeps its checkout root in adapter-private configuration. That root is operational configuration, not repository identity.

## Events and automations

`RepositoryEventBridge` converts verified #44 `ConnectorEvent` evidence into canonical platform events.

Repository events are scoped to their canonical Project and preserve repository ID, connection ID, external-resource references and connector provenance. The canonical event ID is deterministic over connection, connector type and connector `dedupe_key`, so repeated webhook deliveries are idempotent.

Unverified connector events are rejected by default.

The integration test exercises the complete path:

`verified repository ConnectorEvent -> RepositoryEventBridge -> canonical PlatformEvent -> AutomationService -> canonical Task`

Duplicate deliveries preserve the same canonical event/delivery identity and do not generate duplicate Tasks.

## Control Plane, CLI and frontend hooks

`register_repository_control_plane(...)` registers the canonical `repositories` resource collection and these policy-enforced commands:

- `repository.status`
- `repository.diff`
- `repository.fetch`
- `repository.branch.create`
- `repository.checkout`
- `repository.commit`
- `repository.push`

Every command delegates to `RepositoryService`; the Control Plane does not call adapters directly.

The platform CLI consumes the same registered extension contract. Repository resources are inspectable through the generic canonical extension commands, for example:

```text
platform extension list repositories
platform extension show repositories <external_resource_id>
platform extension commands
```

The frontend uses the existing extension-collection seam rather than a provider-specific endpoint. `RepositoryCollectionClient` is a typed read hook built on `ControlPlaneCollectionClient` and therefore reads only `/api/v1/repositories` resources exposed by the running Control Plane.

Provider-specific URLs, APIs and SDKs are never used by these northbound clients.

## Local Git baseline

The deterministic Local Git coverage includes:

- initialize and reopen;
- branch creation and checkout;
- branch/tag inspection;
- status and diff;
- commit;
- exact revision lookup and exact tree reads;
- fetch and push primitives;
- missing-Git/provider-unavailable handling;
- exact-revision Workspace materialization;
- changed-file and manifest Artifact return;
- provider replacement without canonical type/identity changes;
- `SecretReference` and clone-path isolation;
- authorization-denied push before side effect;
- canonical external-resource serialization;
- verified event normalization and deterministic deduplication.

## Hosted/self-hosted provider behavior

The connector-backed conformance fixture proves that repository discovery, metadata, exact revisions, tree content, branches/tags and status can be supplied through a generic `ConnectorProvider` without introducing forge-specific canonical types. Unsupported operations fail closed before invocation when they are not advertised.

Issue/PR/MR support remains capability-driven: a forge connector may implement the provider-neutral issue/change-request capability contracts without altering core domain models. Implementing every forge and every optional collaboration operation is intentionally outside #82.

## Security and portability invariants

The integration preserves these invariants:

1. Canonical repository identity is an external-resource ID, never a provider ID or filesystem path.
2. Moving refs are resolved to immutable commit SHAs before Workspace/Run provenance is recorded.
3. Credential material remains behind `SecretReference` and canonical Connection boundaries.
4. Side effects are authorized by operation semantics, not by provider brand.
5. Repository events are verified and deduplicated before entering automation flows.
6. Provider replacement cannot change canonical repository identity or leak provider-native model types into Agent/Task/Workspace contracts.
7. Run provenance and durable repository registration survive process restart without serializing live adapters or secrets.
