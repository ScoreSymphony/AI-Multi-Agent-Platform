# Release Process

## Version policy

- `0.1.0` is the first usable single-node prototype and requires the M2 acceptance gate in #252.
- `1.0.0` is the operational baseline and requires the full conformance gate in #46.
- Patch versions contain backward-compatible fixes.
- Minor versions add backward-compatible capabilities.
- Major versions may change public or canonical contracts and require migration guidance.

Optional ecosystem and advanced-deployment capabilities do not redefine the ordinary local/self-hosted baseline unless a release explicitly claims support for the corresponding profile.

The detailed release-manifest, dependency/provenance/SBOM, compatibility-state, upstream-update and fail-closed gate rules are defined in [`RELEASE_AND_UPSTREAM_POLICY.md`](RELEASE_AND_UPSTREAM_POLICY.md). Security-sensitive releases additionally follow [`SECURITY_HOTFIX_RELEASE_RUNBOOK.md`](SECURITY_HOTFIX_RELEASE_RUNBOOK.md). These documents extend this publication process; they do not create a separate versioning authority.

## Current status

> Status snapshot: 2026-09-06

The #252 usable single-node prototype gate has passed and is maintained by the repository's prototype-acceptance profiles. This satisfies the major functional prerequisite previously assigned to the planned `0.1.0` release.

No GitHub release or semantic-version tag has been published yet. Passing #252 therefore does **not** by itself mean that `0.1.0` has been released: publication still requires an exact release commit, the checklist below, current changelog/provenance and verification of the produced artifacts.

The release/update system itself is no longer only policy documentation. The merged #42 work provides release-manifest validation, compatibility inventory, advisory upstream discovery, fail-closed adoption/release gates, operator-visible release status and the upstream review workflow. PR #492 further hardened this foundation with manifest schema v2, cryptographic dependency/artifact provenance, typed gate evidence, exact source-commit binding and complete canonical `VersionSnapshot` compatibility state.

The remaining #42 operationalization is implemented through deterministic `platform-release generate`, restart-persistent reviewed advisory discovery, explicit schema-v2 browser types and an optional provider-neutral scheduled Git discovery workflow. Discovery remains advisory: it cannot mutate production pins, approve or merge changes, deploy a release or replace #41 as the authority for persisted upgrade/version state.

The `1.0.0` operational target remains gated by #46 full platform conformance. Current open implementation work may add capabilities before that point, but compatibility must not be claimed beyond the profiles that have explicit evidence.

## Release candidate checklist

- [ ] The target release's required issues and explicitly claimed capability profiles have no unresolved release blockers.
- [ ] The relevant acceptance or conformance gate passed on the exact release commit.
- [ ] Required CI, compatibility, CodeQL and dependency-review checks passed.
- [ ] Database, configuration and public-contract migrations are documented.
- [ ] Backup, restore and rollback procedures were exercised where affected.
- [ ] Supported installation and upgrade paths were tested from documented instructions.
- [ ] Upstream revisions, licenses, notices and provenance records are current.
- [ ] Exact dependency lockfiles or resolved dependency sets are frozen and cryptographically bound to the release manifest.
- [ ] Security findings were triaged and no known release-blocking issue remains.
- [ ] `CHANGELOG.md` contains user-visible changes, known limitations and security notes.
- [ ] Package, container and other published artifacts use the same version and source revision.
- [ ] The compatibility/acceptance report names the exact enabled optional profiles rather than implying untested support.
- [ ] The compatibility matrix records the complete canonical `VersionSnapshot` for the tested combination.
- [ ] A release-manifest v2 with typed evidence passes `platform-release validate`.

## Publication

1. Select the exact source commit intended for release.
2. Run the required acceptance/conformance profile and release checks against that commit.
3. Create a release candidate without modifying the already-tested source tree.
4. Freeze exact dependency lock/resolved sets and record their cryptographic digests.
5. Verify generated artifacts and checksums.
6. Populate a reviewed generation-input document with the exact SBOM/provenance references and typed gate evidence, then run `platform-release generate` for the exact release commit and validate the generated manifest.
7. Tag the accepted commit with the semantic version.
8. Publish a GitHub release using the matching changelog section.
9. Record artifact checksums, canonical source revision and tested compatibility profiles.
10. Verify a clean installation using the published artifacts.

`platform-release generate` performs deterministic assembly and hashing; it does not infer that a gate passed or synthesize approval evidence. Missing, stale or failed required evidence therefore continues to block the generated release manifest rather than weakening the manifest-v2 contract.

## Upstream discovery

The optional scheduled discovery workflow resolves Git remote HEADs without cloning or mutating the production baseline, emits provider-neutral observation JSON and evaluates it through the same advisory discovery contract. Changed revisions remain `unknown` until reviewed. A reviewed report can be persisted explicitly with `platform-release upstream-check --data-dir <path> --reviewed-at <timestamp>` so the Control Plane and Settings UI retain candidate state across restarts. This persistence is separate from #41 version state and never performs update adoption.

## Rollback

Every release with migrations or persisted-state changes must document whether rollback is supported, which backup is required and which data may become unreadable by an older version. A failed publication is withdrawn or marked clearly; an already consumed version is superseded with a new patch rather than silently replacing artifacts under the same version.
