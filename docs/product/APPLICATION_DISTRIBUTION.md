# Application packaging and distribution

Application distribution is a platform-owned domain for products produced by user projects. It is intentionally separate from platform self-update/release policy (`release`), Registry/Marketplace package distribution, and portable resource import/export.

The canonical flow is:

`Project/Workspace -> BuildSpecification -> canonical Task/Run -> isolated materialization -> File/Artifact -> gates -> manifest -> ApplicationReleasePublisher`

## Canonical ownership

`ApplicationRelease` owns application version/channel/visibility, exact source revision, exact build-spec revision, target states, canonical Artifact/File references, build Task/Run provenance, gate evidence and published links. Provider-native release IDs and asset IDs are retained only under namespaced external metadata.

`BuildSpecification` is language/toolchain neutral. A target names its OS, architecture, package type, output path and capability requirements. The reference command path stores the build command as an argv tuple and never turns it into a shell string.

`ApplicationDistributionService.request_build()` creates a canonical Task and Run for each target, binds the Run to the release's immutable Workspace snapshot and persists that Run-to-release association before dispatch. The local/reference composition routes these build Runs through `ApplicationBuildLifecycleBackend` and `ApplicationCommandExecutor`; ordinary agent Runs keep their existing lifecycle unchanged.

When distributed execution is enabled and a canonical `DistributedRuntime` is available, deployment composition instead binds the same application-build Runs to `DistributedApplicationBuildLifecycleBackend` and `DistributedBuildTargetMatcher`. The backend translates the already-created canonical Run into a `WorkerJobRequest` and delegates placement, dispatch, Workspace transfer, Worker lifecycle, reconciliation and artifact publication to the normal #14/#37 distributed boundaries. Application-release identity, Task/Run identity and Workspace/source provenance therefore remain canonical and do not become Worker/provider identities.

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

For the local/reference build path, #748 is therefore complete: declared `SecretReference` values are resolved late, delivered only ephemerally to the executor and never persisted as plaintext application-release state. The distributed backend deliberately has a narrower current boundary. If `secret_environment` is non-empty, `DistributedApplicationBuildLifecycleBackend` fails before Worker dispatch with `UNSUPPORTED_CAPABILITY` until scoped Worker secret delivery exists. It does not downgrade those references into plaintext Worker metadata and does not silently execute without the required secret-backed inputs. Non-secret remote builds remain supported.

## Target placement

Targets have independent states. The local/reference command-build path uses `LocalBuildTargetMatcher`, which only claims support when the actual host OS/architecture and required local executable/capabilities match the target.

With distributed execution enabled, `DistributedBuildTargetMatcher` asks the canonical scheduler about the same Worker/Node inventory used for dispatch. Target requirements include OS, architecture, the application-build capability plus declared target capabilities/resource hints. `DistributedApplicationBuildLifecycleBackend` then dispatches the canonical Run through `DistributedRuntime` to the selected compatible Worker. Multi-target releases may select different Workers for different targets while preserving one canonical `ApplicationRelease` identity.

An unsupported target remains explicit and is not given fabricated Task/Run provenance. One successful target while another is pending/failed/unsupported is represented as a partial release; it is not promoted to ready. Publication is fail-closed until every required target succeeded, each target has exactly one accepted canonical artifact, and every configured release gate exists with passing current evidence.

The package-type enum includes executable, archive, installer, Linux package, macOS bundle, OCI image reference, source archive and static-web bundle. Enum membership alone is not a compatibility claim; a target is supported only when its actual execution path proves the required host/tool capabilities.

## Distributed Workspace, recovery and result admission

Remote builds keep the immutable release Workspace/source snapshot as the execution source of truth. The distributed Workspace path materializes the requested snapshot on the selected Worker without replacing canonical Workspace identity with a Worker-local path. Successful Worker output is published back through canonical File/Artifact storage before application-release admission.

Remote result admission is fail-closed. The distributed lifecycle checks the returned release/target/build-spec/source/Workspace identity, requires exactly the expected canonical changed File/Artifact for the declared output, verifies its checksum and only then exposes the Artifact through the canonical Run and release. Worker/Node/runtime provenance may be retained as bounded metadata, while provider-private filesystem paths do not become canonical identity.

Dispatch is idempotent around the canonical Run: the Worker job identity is deterministic, repeated starts for the same Run reconcile the existing job instead of creating a second logical build, and uncertain dispatch/result replies surface as retryable availability failures rather than causing blind redispatch. Worker disconnect/reconnect and cancel-pending recovery use the distributed runtime's normal reconciliation path and preserve the same canonical job. This is recovery/reconciliation support; it is not a claim that every possible remote transport or Worker failure can be transparently hidden.

## Manifest and update seam

`release_manifest()` generates deterministic machine-readable content. Artifacts include canonical Artifact/File IDs, target, package/media type, SHA-256, build Task/Run provenance, evidence references and, after publication, provider-returned download URLs. Deterministic JSON bytes and a manifest SHA-256 are available for publication or future update-discovery clients.

The versioned schema lives in `docs/schemas/application-release-manifest.schema.json`. It contains enough version/channel/target/digest information for a later explicit updater without implementing automatic application updates in this issue. Build environment values and SecretReferences are intentionally not copied into the public release manifest; only the build specification identity/revision is exposed there.

## Release gates and maintained conformance

#750 binds publication readiness to current canonical gate evidence instead of trusting a historical build flag. `ApplicationReleaseGateCoordinator` supports deterministic checks and, when configured, canonical Verification and Evaluation evidence plus package-smoke evidence. Missing, stale, conflicting, inconclusive or failed mandatory evidence blocks publication; persisted Verification evidence can be reconstructed after restart.

The maintained acceptance bundle from #751 is documented in [`APPLICATION_DISTRIBUTION_CONFORMANCE.md`](APPLICATION_DISTRIBUTION_CONFORMANCE.md). It consumes the productive #749 distributed-build fixtures rather than treating remote dispatch as future work, and also covers the #748 secret boundary, #750 gate behavior, GitHub Releases reference-provider failure/idempotency paths, Control Plane security and canonical/provider provenance separation.

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

The current implementation intentionally does not claim a universal installer generator, automatic application updater or signing/notarization implementation. Signing remains a separate capability seam.

Remote/cross-platform application builds are supported only when distributed execution is enabled and the platform has a reachable Worker whose advertised OS, architecture, capabilities, resources and actual toolchain satisfy the target. The platform does not infer buildability from an enum value or fabricate support for an absent macOS/Windows/Linux/architecture-specific builder.

Secret-backed builds remain an explicit asymmetry: the local/reference backend implements #748 late `SecretReference` resolution, while the distributed backend currently rejects non-empty `secret_environment` before dispatch until scoped Worker secret delivery is implemented. That remaining limitation must not be described as a lack of remote Worker build dispatch in general.

The local command executor remains a valid self-hosted reference path when distributed execution is disabled or unnecessary. The distributed path uses the canonical Worker/Workspace/Artifact boundaries rather than replacing the application-release contracts. No recurring paid build or distribution service is required by the reference implementation.
