# Release Process

## Version policy

- `0.1.0` is the first usable single-node prototype and requires the M2 acceptance gate in #252.
- `1.0.0` is the operational baseline and requires the full platform-conformance gate, historically tracked under issue #46.
- Patch versions contain backward-compatible fixes and must not intentionally break documented public surfaces except for unavoidable security/correctness remediation.
- While the repository remains `0.x`, a minor version may contain a documented incompatible change to an explicitly Beta or Experimental surface under [`FEATURE_CLASSIFICATION.md`](FEATURE_CLASSIFICATION.md), but any stricter surface-specific API/schema/format major-version or migration mechanism still applies; Stable surfaces remain protected by their own major-version/deprecation rules.
- At and after `1.0.0`, minor versions remain backward-compatible for the Stable public compatibility set. Beta/Experimental surfaces remain outside that Stable compatibility set and may evolve only under their explicitly documented maturity rules and any stricter surface-specific versioning mechanism.
- A repository/package major version may change Stable public or canonical contracts and requires migration guidance unless the affected Stable surface has its own independent major-version mechanism (for example `/api/v2` or a new schema/contract major).

Repository/release versioning and per-surface compatibility are separate. [`FEATURE_CLASSIFICATION.md`](FEATURE_CLASSIFICATION.md) defines the authoritative **architectural role + stability** vocabulary for public platform surfaces. In particular, repository `0.x` does not make every surface Experimental: a surface classified Stable must follow its own major-version/deprecation rules even before repository `1.0.0`, while Beta/Experimental surfaces may evolve under the bounded rules documented there. Release notes must identify user-visible maturity changes, deprecations and incompatible Beta/Experimental changes.

Optional ecosystem and advanced-deployment capabilities do not redefine the ordinary local/self-hosted baseline unless a release explicitly claims support for the corresponding profile. Role/stability labels likewise do not imply that an optional profile is enabled or conformance-tested; concrete compatibility claims still require evidence for the exact release/profile combination.

The detailed release-manifest, dependency/provenance/SBOM, compatibility-state, upstream-update and fail-closed gate rules are defined in [`operations/RELEASE_AND_UPSTREAM_POLICY.md`](operations/RELEASE_AND_UPSTREAM_POLICY.md). Security-sensitive releases additionally follow [`security/SECURITY_HOTFIX_RELEASE_RUNBOOK.md`](security/SECURITY_HOTFIX_RELEASE_RUNBOOK.md). These documents extend this publication process; they do not create a separate versioning authority.

## Current status

> Status snapshot: 2026-09-20

The #252 usable single-node prototype gate has passed and is maintained by the repository's
prototype-acceptance profiles. The planned `0.1.0` prototype milestone was never formally
published or tagged.

Issue #46, the full platform-conformance gate required by the version policy for the operational
baseline, is complete. Because the repository has progressed beyond the prototype gate without an
intervening formal release, the first formal publication tracked by #1237 now targets **`1.0.0`**.
The project will not create a retroactive `0.1.0` release merely to preserve the old planned
sequence.

Publication is still blocked until the terminal #747 product-readiness audit passes for the exact
release candidate, all release-blocking V1/M3 findings are resolved or explicitly reclassified as
non-blocking, and the complete release checklist below is green for that exact source commit.

Until every remaining source-changing release blocker is resolved, repository/package metadata
intentionally remains on the pre-release development version. The final `1.0.0` projection must
then be performed atomically **before** the terminal #747 audit across package metadata, runtime
`__version__`, canonical `VersionSnapshot.platform_release`/compatibility metadata and shipped
built-in release identity/compatibility surfaces. Historical migration/adoption evidence that
deliberately names `0.0.1` must not be rewritten as part of that projection.

The resulting version-projected commit is the release candidate. #747 and every required
commit-bound CI/security/conformance gate must pass on that exact commit. If a later fix changes the
source tree, the candidate changes and affected checks — plus the terminal audit when material —
must run again. Tagging and publication happen only after that exact versioned candidate passes;
they must not introduce another source-tree change.

The release/update system itself is no longer only policy documentation. The merged #42 work
provides release-manifest validation, compatibility inventory, advisory upstream discovery,
fail-closed adoption/release gates, operator-visible release status and the upstream review
workflow. PR #492 further hardened this foundation with manifest schema v2, cryptographic
dependency/artifact provenance, typed gate evidence, exact source-commit binding and complete
canonical `VersionSnapshot` compatibility state.

The remaining #42 operationalization is implemented through deterministic
`platform-release generate`, restart-persistent reviewed advisory discovery, explicit schema-v2
browser types and an optional provider-neutral scheduled Git discovery workflow. Discovery remains
advisory: it cannot mutate production pins, approve or merge changes, deploy a release or replace
#41 as the authority for persisted upgrade/version state.

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
- [ ] `CHANGELOG.md` contains user-visible changes, known limitations, security notes, public-surface maturity promotions/downgrades and relevant deprecation/migration notes.
- [ ] Package, container and other published artifacts use the same version and source revision.
- [ ] The compatibility/acceptance report names the exact enabled optional profiles rather than implying untested support.
- [ ] The compatibility matrix records the complete canonical `VersionSnapshot` for the tested combination.
- [ ] A release-manifest v2 with typed evidence passes `platform-release validate`.

## Publication

1. Resolve or explicitly defer every remaining source-changing release blocker.
2. Project the target semantic version across all canonical release-version surfaces and commit the complete projection.
3. Treat that versioned commit as the exact release candidate; run #747 and all required acceptance/conformance, CI, compatibility, CodeQL, dependency-review, clean-install and first-run checks against it.
4. If a required fix changes the source tree, create a new candidate commit and rerun every affected commit-bound gate; material changes require a new terminal #747 decision.
5. Once the exact candidate passes, freeze exact dependency lock/resolved sets and record their cryptographic digests.
6. Build release artifacts from that accepted commit and verify their checksums.
7. Populate a reviewed generation-input document with the exact SBOM/provenance references and typed gate evidence, then run `platform-release generate` for the accepted commit and validate the generated manifest.
8. Tag that same accepted commit with the semantic version and publish the GitHub release using the matching changelog section.
9. Record artifact checksums, canonical source revision and tested compatibility profiles.
10. Verify a clean installation using the published artifacts/source.

`platform-release generate` performs deterministic assembly and hashing; it does not infer that a gate passed or synthesize approval evidence. Missing, stale or failed required evidence therefore continues to block the generated release manifest rather than weakening the manifest-v2 contract.

## Upstream discovery

The optional scheduled discovery workflow resolves Git remote HEADs without cloning or mutating the production baseline, emits provider-neutral observation JSON and evaluates it through the same advisory discovery contract. Changed revisions remain `unknown` until reviewed. A reviewed report can be persisted explicitly with `platform-release upstream-check --data-dir <path> --reviewed-at <timestamp>` so the Control Plane and Settings UI retain candidate state across restarts. This persistence is separate from #41 version state and never performs update adoption.

## Rollback

Every release with migrations or persisted-state changes must document whether rollback is supported, which backup is required and which data may become unreadable by an older version. A failed publication is withdrawn or marked clearly; an already consumed version is superseded with a new patch rather than silently replacing artifacts under the same version.
