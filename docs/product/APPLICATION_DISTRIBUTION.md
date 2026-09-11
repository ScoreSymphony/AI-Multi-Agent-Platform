# Application packaging and distribution

Application distribution is a platform-owned domain for products produced by user projects. It is intentionally separate from platform self-update/release policy (`release`), Registry/Marketplace package distribution, and portable resource import/export.

The canonical flow is:

`Project/Workspace -> BuildSpecification -> canonical Task/Run -> isolated materialization -> File/Artifact -> gates -> manifest -> ApplicationReleasePublisher`

## Canonical ownership

`ApplicationRelease` owns application version/channel/visibility, exact source revision, exact build-spec revision, target states, canonical Artifact/File references, build Task/Run provenance, gate evidence and published links. Provider-native release IDs and asset IDs are retained only under namespaced external metadata.

`BuildSpecification` is language/toolchain neutral. A target names its OS, architecture, package type, output path and capability requirements. The reference command path stores the build command as an argv tuple and never turns it into a shell string.

`ApplicationDistributionService.request_build()` creates a canonical Task and Run for each target, binds the Run to the release's immutable Workspace snapshot and persists that Run-to-release association before dispatch. The shipped single-node profile routes these build Runs through a dedicated `ApplicationBuildLifecycleBackend` and `ApplicationCommandExecutor`; ordinary agent Runs keep their existing lifecycle unchanged.

The command executor runs explicit argv with `create_subprocess_exec()` inside an isolated materialized Workspace copy. It starts from a small host-environment allowlist and adds only the explicit build environment delivered through the execution contract; it never inherits the arbitrary parent process environment. It uses no shell, rejects paths escaping the materialization and requires the declared `output_path` to exist as a regular file after a successful process exit.

After execution, the Workspace provider captures the declared output as a canonical File, verifies its digest and links a deterministic canonical Artifact ID. The service then attaches that Artifact to the canonical Run and admits it into the release only after the Run has succeeded and the immutable Workspace binding, File linkage and checksum all agree. An arbitrary local path therefore cannot be promoted directly into release state.

Build execution itself is a high-risk external side effect. When the authorization/Approval runtime is present, the proposed action is bound to the release, target, exact argv, source path, output path, source revision, Workspace snapshot, explicit non-secret environment and referenced secrets before dispatch. Build permission and publication permission are separate.

## Build environment and SecretReferences

`BuildSpecification.environment` contains explicit non-secret string environment values. Environment names that look sensitive under the central security redaction policy, such as token/password/API-key variables, are rejected from this plaintext field and must use `BuildSpecification.secret_environment` instead.

`BuildSpecification.secret_environment` maps an environment variable name to a canonical `SecretReference`. The reference is durable application-release metadata; the secret material is not. The Control Plane and JSON application-release repository serialize only provider/reference/scope/version metadata.

The local application build lifecycle resolves each secret as late as possible, after the canonical Task/Run and immutable Workspace binding are known and immediately before materialization/execution. Resolution uses the configured canonical `SecretProvider` with a `SecretAccessContext` bound to:

- the dedicated application-build secret consumer identity;
- release project and Workspace;
- canonical Task and Run;
- `application.build.command` action/capability;
- the explicit application-build purpose;
- a bounded requested lifetime derived from the build timeout.

The reference scope must match the release project. Missing SecretProvider support, unresolved/revoked references or authorization failures therefore fail closed instead of falling back to ambient host credentials.

Resolved values exist only in the ephemeral `ExecutionRequest.environment` supplied to the executor. The executor records only the names of secret-backed environment entries in its safe policy context so their resolved values can be removed from captured stdout/stderr with the central text-redaction helper. Secret material is not copied into `ApplicationRelease`, Task objectives, manifests, artifact metadata or persisted application-release retry/recovery state.

The normal single-node/server composition gives secret resolution a dedicated service principal with only the `INVOKE_SENSITIVE_CAPABILITY`/`SECRET_REFERENCE` permission. This avoids expanding the ordinary application-build Run principal into a general credential-management identity.

## Target placement

Targets have independent states. The shipped single-node command-build path uses `LocalBuildTargetMatcher`, which only claims support when the actual host OS/architecture and required local executable/capabilities match the target.

`DistributedBuildTargetMatcher` exists as a provider-neutral placement seam over canonical Worker/Node inventory, but the current reference command-build lifecycle does not yet dispatch the command to a remote Worker. Distributed inventory therefore must not be used to claim that a target is buildable until remote build execution is actually bound to that Worker path.

One successful target while another is pending/failed is represented as a partial release; it is not promoted to ready. Publication is fail-closed until every target succeeded, each target has exactly one accepted canonical artifact, and every configured pre-build/test/post-build gate exists with `passed` status.

The package-type enum includes executable, archive, installer, Linux package, macOS bundle, OCI image reference, source archive and static-web bundle. Enum membership alone is not a compatibility claim; a target is supported only when its actual execution path proves the required host/tool capabilities.

## Manifest and update seam

`release_manifest()` generates deterministic machine-readable content. Artifacts include canonical Artifact/File IDs, target, package/media type, SHA-256, build Task/Run provenance, evidence references and, after publication, provider-returned download URLs. Deterministic JSON bytes and a manifest SHA-256 are available for publication or future update-discovery clients.

The versioned schema lives in `docs/schemas/application-release-manifest.schema.json`. It contains enough version/channel/target/digest information for a later explicit updater without implementing automatic application updates in this issue. Build environment values and SecretReferences are intentionally not copied into the public release manifest; only the build specification identity/revision is exposed there.

## Publishing

`ApplicationReleasePublisher` is the only publication boundary used by canonical release logic. A publisher exposes `preview()` and `publish()`. `preview()` is side-effect free. `publish()` is an external side effect and requires the canonical publication authorization path.

`GitHubReleasePublisher` is the first provider-specific publisher. The destination is selected per operation through safe `publisher_configuration` containing a canonical `connection_id` and `repository_ref`; the publisher is not permanently tied to one repository. Embedded credentials are rejected from this configuration.

The publisher calls `ConnectorService.invoke_action()` for `github.release.create` and `github.release.asset.attach`. The concrete `GitHubReleaseConnectorProvider` implements those operations with the GitHub REST API without requiring a GitHub SDK. It resolves the token only through the canonical SecretProvider, validates repository visibility, resolves the exact source revision/tag, creates or reuses the release fail-closed, uploads the deterministic manifest and `SHA256SUMS`, streams canonical File bytes for application assets, verifies SHA-256 before upload and returns provider-native IDs only as namespaced metadata.

The shipped single-node profile installs `DurableGitHubReleaseConnectorProvider` when a SecretProvider is configured. After process restart it rehydrates a persisted canonical Connection from `ConnectorRepository` before the first GitHub action, instead of treating process-local adapter state as canonical. Its service principal receives only the secret-resolution capability needed by the connector.

Private GitHub repository releases are represented as authenticated/private rather than public downloads. Conversely, GitHub does not provide a per-release private visibility layer inside a public repository, so requesting a non-public release there fails instead of producing a misleading visibility claim.

Idempotency is explicit. Creating the same application/version/channel with identical provenance resolves the existing canonical release. A conflicting source/build specification fails. The Run-to-release mapping is unique and durable. Re-publishing an already published release through the same provider is a no-op; publishing through a different provider conflicts. Existing tags, releases and assets are accepted only when their source/digest evidence agrees with the canonical release.

## Control Plane

`register_application_distribution_control_plane()` registers `application-releases` plus the following commands:

- `application-release.create`
- `application-release.build`
- `application-release.preview`
- `application-release.publish`

These use the normal versioned extension surface, including Control Plane idempotency keys for mutations. `application-release.create` accepts explicit non-secret `environment` values and typed `secret_environment` SecretReference objects inside `build_specification`. `application-release.build` accepts an optional `approval_id` for the exact high-risk build proposal; that proposal includes both environment configuration and SecretReference metadata, never resolved secret material. Preview/publish accept safe `publisher_configuration`; publication forwards `approval_id` through both the application-release authorization gate and the Connector authorization boundary.

Clients should present `visibility`, immutable `release_url`, per-artifact `download_url`, checksums and target metadata distinctly. A private/authenticated URL must never be described as public.

## Reference limitations

The current reference implementation intentionally does not claim a universal installer generator, automatic application updater, signing/notarization implementation or remote cross-OS build farm. Signing remains a separate capability seam.

The local command executor is a reference execution path for self-hosted builds; it is not a replacement for the broader Executor/Worker sandbox architecture. Remote Worker build dispatch can replace this path later without changing `ApplicationRelease`, `BuildSpecification`, manifest or publisher contracts. The secret-environment contract is also designed so remote execution can carry SecretReferences/scoped delivery metadata rather than persisting plaintext material in canonical Worker jobs. No recurring paid build/distribution service is required by the reference implementation.
