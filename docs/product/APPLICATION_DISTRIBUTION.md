# Application packaging and distribution

Application distribution is a platform-owned domain for products produced by user projects. It is intentionally separate from platform self-update/release policy (`release`), Registry/Marketplace package distribution, and portable resource import/export.

The canonical flow is:

`Project/Workspace -> BuildSpecification -> canonical Task/Run -> File/Artifact -> gates -> manifest -> ApplicationReleasePublisher`

## Canonical ownership

`ApplicationRelease` owns application version/channel/visibility, exact source revision, exact build-spec revision, target states, canonical Artifact/File references, build Task/Run provenance, gate evidence and published links. Provider-native release IDs and asset IDs are retained only under namespaced external metadata.

`BuildSpecification` is language/toolchain neutral. A target names its OS, architecture, package type, output path and capability requirements. Build commands or workflow references are configuration; they are not executed by a release-only shell path. `ApplicationDistributionService.request_build()` creates a normal canonical Task for each target so normal planning, Executor/Worker selection, Workspace materialization, logs and authorization remain authoritative.

A build output becomes releasable only through `record_build_artifact()`. That operation requires a succeeded canonical Run, requires the Artifact to be attached to that Run, requires the canonical File to be linked to the same Artifact, and re-verifies the File checksum before accepting the digest. This prevents an arbitrary local path from being promoted into release state.

## Gates and partial builds

Targets have independent states. One successful target while another is pending/failed is represented as a partial release; it is not promoted to ready. Publication is fail-closed until every target succeeded, each target has exactly one accepted canonical artifact, and every configured pre-build/test/post-build gate exists with `passed` status.

The initial package types are executable, archive, installer, Linux package, macOS bundle, OCI image reference, source archive and static-web bundle. Their presence in the enum is not a support claim: scheduling/build logic must still match the target's declared capabilities to an actually available Worker/Node.

## Manifest and update seam

`release_manifest()` generates deterministic machine-readable content. Artifacts include canonical Artifact/File IDs, target, package/media type, SHA-256, build Task/Run provenance, evidence references and, after publication, provider-returned download URLs. Deterministic JSON bytes and a manifest SHA-256 are available for publication or future update-discovery clients.

The versioned schema lives in `docs/schemas/application-release-manifest.schema.json`. It contains enough version/channel/target/digest information for a later explicit updater without implementing automatic application updates in this issue.

## Publishing

`ApplicationReleasePublisher` is the only publication boundary used by canonical release logic. A publisher exposes `preview()` and `publish()`. `preview()` is side-effect free. `publish()` is an external side effect.

`GitHubReleasePublisher` is the first provider-specific adapter. It uses `ConnectorService.invoke_action()` and therefore inherits Connection scoping, credential SecretReferences, authorization and optional Approval handling from the canonical Connector boundary. It does not import a GitHub SDK and does not store credentials in release metadata. The adapter requires connector actions `github.release.create` and `github.release.asset.attach`; a GitHub Connector implementation owns the provider API details.

Idempotency is explicit. Creating the same application/version/channel with identical provenance resolves the existing canonical release. A conflicting source/build specification fails. Re-requesting a target build after its canonical Task is bound returns existing state. Re-publishing an already published release through the same provider is a no-op; publishing through a different provider conflicts. Provider adapters additionally request fail-if-different tag/release/asset semantics.

## Control Plane

`register_application_distribution_control_plane()` registers `application-releases` plus the following commands:

- `application-release.create`
- `application-release.build`
- `application-release.preview`
- `application-release.publish`

These use the normal versioned extension surface, including required Control Plane idempotency keys for mutations. Publication forwards `approval_id` into the Connector authorization boundary. Clients should present `visibility`, immutable `release_url`, per-artifact `download_url`, checksums and target metadata distinctly; a private/authenticated URL must never be described as public.

## Reference limitations

This first vertical slice deliberately does not introduce a universal installer generator or a release-specific command runner. Concrete toolchains remain normal executor/workflow capabilities. The GitHub adapter defines the safe publication contract over Connector actions; a concrete GitHub Connector that performs HTTP release/tag/asset calls can implement those actions without changing the canonical release model.
